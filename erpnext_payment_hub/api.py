from __future__ import annotations

from urllib.parse import urlencode

import frappe
from frappe.exceptions import TimestampMismatchError
from frappe.utils import flt

from erpnext_payment_hub.gateway import (
    create_gateway_transaction,
    get_provider,
    get_provider_account,
    update_transaction_from_status,
    _customer_from_reference,
    get_pos_context,
    resolve_payment_terminal,
    resolve_pos_station,
    touch_pos_station,
)




def _payment_return_redirect(status=None, transaction=None, message=None):
    """Redirect hosted-checkout browser returns to a customer-friendly status page."""
    normalized = (status or "").strip().lower()
    if normalized in ("captured", "paid", "success", "successful"):
        state = "paid"
    elif normalized in ("failed", "declined", "abandoned", "cancelled", "canceled"):
        state = "failed"
    elif normalized in ("expired",):
        state = "expired"
    elif normalized in ("pending", "initiated", "waiting"):
        state = "pending"
    else:
        state = "invalid"

    params = {"state": state}
    if transaction:
        params["reference"] = transaction
    if message:
        params["message"] = message

    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = (
        f"{frappe.utils.get_url()}/payment_hub_status?{urlencode(params)}"
    )
    return None


def _default_return_url():
    return (
        f"{frappe.utils.get_url()}/api/method/"
        "erpnext_payment_hub.api.payment_return"
    )


@frappe.whitelist()
def create_payment(
    reference_doctype,
    reference_name,
    amount,
    payment_method="ALL",
    provider_account=None,
    currency="KWD",
    return_url=None,
    cancel_url=None,
    computer_name=None,
    pairing_code=None,
):
    amount = flt(amount, 3)
    if amount <= 0:
        frappe.throw("Payment amount must be greater than zero.")

    account = get_provider_account(provider_account, for_new_transaction=True)
    provider = get_provider(account)
    customer = _customer_from_reference(reference_doctype, reference_name)
    pos_context = get_pos_context(reference_doctype, reference_name)

    station = resolve_pos_station(
        computer_name=computer_name,
        pairing_code=pairing_code,
        pos_profile=pos_context.pos_profile,
        branch=pos_context.branch,
    )

    if station:
        if station.provider_account != account.name:
            account = get_provider_account(station.provider_account, for_new_transaction=True)
            provider = get_provider(account)

        terminal = (
            frappe.get_doc("Payment Terminal", station.payment_terminal)
            if station.payment_terminal
            else None
        )
        touch_pos_station(station)
    else:
        terminal = resolve_payment_terminal(
            provider_account=account,
            pos_profile=pos_context.pos_profile,
            branch=pos_context.branch,
            company=pos_context.company,
            warehouse=pos_context.warehouse,
        )

    base_return = return_url or _default_return_url()
    cancel = cancel_url or base_return

    webhook_url = (
        f"{frappe.utils.get_url()}/api/method/"
        f"erpnext_payment_hub.webhook.{_webhook_method(account.provider)}"
        f"?provider_account={account.name}"
    )

    normalized = provider.create_payment(
        amount=amount,
        currency=currency,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        payment_method=payment_method,
        customer=customer,
        return_url=base_return,
        cancel_url=cancel,
        webhook_url=webhook_url,
        terminal=terminal,
        pos_context=pos_context,
    )

    txn = create_gateway_transaction(
        transaction_type="Payment",
        provider_account=account,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        payment_method=payment_method,
        amount=amount,
        currency=currency,
        normalized=normalized,
        branch=pos_context.branch,
        pos_profile=pos_context.pos_profile,
        payment_terminal=getattr(terminal, "name", None) if terminal else None,
        terminal_id=getattr(terminal, "terminal_id", None) if terminal else None,
        pos_station=getattr(station, "name", None) if station else None,
        computer_name=getattr(station, "computer_name", None) if station else computer_name,
    )

    return {
        "transaction": txn.name,
        "provider": account.provider,
        "status": txn.status,
        "payment_url": txn.payment_url,
    }


def _webhook_method(provider):
    return {
        "Tap Payments": "tap",
        "MyFatoorah": "myfatoorah",
        "UPayments": "upayments",
    }[provider]


@frappe.whitelist()
def refresh_transaction(transaction_name):
    doc = frappe.get_doc("Gateway Transaction", transaction_name)
    account = get_provider_account(doc.provider_account)
    provider = get_provider(account)

    if doc.transaction_type == "Refund" and hasattr(provider, "get_refund_status"):
        normalized = provider.get_refund_status(doc)
    else:
        normalized = provider.get_payment_status(doc)

    update_transaction_from_status(doc, normalized)

    return {
        "transaction": doc.name,
        "status": doc.status,
        "provider": doc.provider,
    }


@frappe.whitelist()
def refund_transaction(
    transaction_name,
    amount,
    reason=None,
    reference_doctype=None,
    reference_name=None,
):
    original = frappe.get_doc("Gateway Transaction", transaction_name)

    if original.transaction_type != "Payment":
        frappe.throw("Only an original payment transaction can be refunded.")

    if original.status != "Captured":
        frappe.throw(
            f"Only captured transactions can be refunded. Current status: {original.status}"
        )

    amount = flt(amount, 3)
    if amount <= 0:
        frappe.throw("Refund amount must be greater than zero.")

    # When a return invoice is supplied, it becomes the idempotency anchor for
    # this source transaction. Reopening/retrying the same return must never send
    # a second provider refund.
    if reference_name:
        existing = frappe.get_all(
            "Gateway Transaction",
            filters={
                "transaction_type": "Refund",
                "original_transaction": original.name,
                "reference_doctype": reference_doctype or original.reference_doctype,
                "reference_name": reference_name,
            },
            fields=["name", "amount", "status", "provider"],
            order_by="creation asc",
        )
        for row in existing:
            if abs(flt(row.amount, 3) - amount) <= 0.0005:
                return {
                    "refund_transaction": row.name,
                    "original_transaction": original.name,
                    "provider": row.provider or original.provider,
                    "status": row.status,
                    "amount": amount,
                    "reused": True,
                }

    available = flt(original.amount, 3) - flt(original.refunded_amount, 3)
    if amount > available + 0.0005:
        frappe.throw(f"Maximum refundable amount is {available:.3f} {original.currency}.")

    # CRITICAL: refund through the ORIGINAL provider account, never the current default.
    account = get_provider_account(original.provider_account)
    if account.status == "Disabled":
        frappe.throw(
            f"{account.name} is disabled. Set it to Refund Only, Active or Test "
            "before refunding its old transactions."
        )

    provider = get_provider(account)
    normalized = provider.refund(original, amount, reason=reason)

    refund = create_gateway_transaction(
        transaction_type="Refund",
        provider_account=account,
        reference_doctype=reference_doctype or original.reference_doctype,
        reference_name=reference_name or original.reference_name,
        original_transaction=original.name,
        payment_method=original.payment_method,
        amount=amount,
        currency=original.currency,
        normalized=normalized,
        branch=original.branch,
        pos_profile=original.pos_profile,
        payment_terminal=original.payment_terminal,
        terminal_id=original.terminal_id,
        pos_station=original.pos_station,
        computer_name=original.computer_name,
        pos_payment_session=getattr(original, "pos_payment_session", None),
        pos_payment_allocation=getattr(original, "pos_payment_allocation", None),
    )

    # Reserve the amount immediately to block duplicate over-refunds. The source
    # payment can also be touched by callbacks/reconciliation, so retry against
    # the latest row if Frappe optimistic locking detects a concurrent update.
    for attempt in range(3):
        try:
            original.reload()
            current = flt(original.refunded_amount, 3)
            original.refunded_amount = flt(current + amount, 3)
            original.save(ignore_permissions=True)
            break
        except TimestampMismatchError:
            if attempt >= 2:
                raise

    return {
        "refund_transaction": refund.name,
        "original_transaction": original.name,
        "provider": original.provider,
        "status": refund.status,
        "amount": amount,
        "reused": False,
    }


@frappe.whitelist(allow_guest=True)
def payment_return(**kwargs):
    """Browser redirect landing endpoint.

    For UPayments, use callback identifiers only to locate the ERPNext record,
    then verify the real payment status server-to-server before updating it.
    """
    params = dict(frappe.form_dict)

    # MyFatoorah v3 redirect flow appends paymentId.
    myfatoorah_payment_id = (
        params.get("paymentId")
        or params.get("PaymentId")
        or params.get("payment_id")
    )
    if myfatoorah_payment_id:
        # MyFatoorah hosted checkout returns PaymentId only after the payment
        # attempt. The original ERPNext transaction therefore cannot be found
        # by PaymentId yet. Query MyFatoorah first, obtain InvoiceId, then match
        # the pending Gateway Transaction by provider_order_id.
        candidate_accounts = frappe.get_all(
            "Payment Provider Account",
            filters={"provider": "MyFatoorah"},
            pluck="name",
        )

        for account_name in candidate_accounts:
            try:
                account = get_provider_account(account_name)
                provider = get_provider(account)
                normalized = provider.get_payment_status_by_payment_id(
                    myfatoorah_payment_id
                )
            except Exception:
                continue

            invoice_id = normalized.get("provider_order_id")
            if not invoice_id:
                continue

            name = frappe.db.get_value(
                "Gateway Transaction",
                {
                    "provider_account": account.name,
                    "provider_order_id": invoice_id,
                    "transaction_type": "Payment",
                },
                "name",
            )
            if not name:
                continue

            doc = frappe.get_doc("Gateway Transaction", name)
            update_transaction_from_status(doc, normalized)
            return _payment_return_redirect(doc.status, doc.name)

    # Tap redirect flow returns the Charge ID as tap_id.
    tap_id = params.get("tap_id")
    if tap_id:
        name = frappe.db.get_value(
            "Gateway Transaction",
            {
                "provider": "Tap Payments",
                "provider_transaction_id": tap_id,
                "transaction_type": "Payment",
            },
            "name",
        )
        if name:
            doc = frappe.get_doc("Gateway Transaction", name)
            account = get_provider_account(doc.provider_account)
            normalized = get_provider(account).get_payment_status(doc)
            update_transaction_from_status(doc, normalized)
            return _payment_return_redirect(doc.status, doc.name)

    track_id = params.get("track_id") or params.get("trackId")
    requested_order_id = (
        params.get("requested_order_id")
        or params.get("requestedOrderId")
        or params.get("order_id")
        or params.get("orderId")
    )

    if track_id or requested_order_id:
        filters = {
            "provider": "UPayments",
            "transaction_type": "Payment",
        }
        if requested_order_id:
            filters["provider_order_id"] = requested_order_id

        name = frappe.db.get_value("Gateway Transaction", filters, "name")

        if name:
            doc = frappe.get_doc("Gateway Transaction", name)
            if track_id:
                doc.provider_tracking_id = track_id
                doc.save(ignore_permissions=True)

            account = get_provider_account(doc.provider_account)
            normalized = get_provider(account).get_payment_status(doc)
            update_transaction_from_status(doc, normalized)

            return _payment_return_redirect(doc.status, doc.name)

    return _payment_return_redirect(
        "invalid",
        message="We could not match this payment return to a Payment Hub transaction. Please contact the cashier.",
    )

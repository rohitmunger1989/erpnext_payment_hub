from __future__ import annotations

import frappe
from frappe.utils import add_to_date, cint, flt, now_datetime

from erpnext_payment_hub.gateway import (
    create_gateway_transaction,
    get_provider,
    resolve_payment_terminal,
    resolve_pos_station,
    touch_pos_station,
    update_transaction_from_status,
)
from erpnext_payment_hub.pos.scope import (
    apply_session_shift_context,
    build_scope_context,
    effective_pos_profile,
    validate_shift_scope,
)

from erpnext_payment_hub.pos.service import (
    FAILED_SESSION_STATUSES,
    PAID_PENDING_SESSION_STATUSES,
    PENDING_SESSION_STATUSES,
    allocation_rows,
    assert_amount_available,
    assert_open_session,
    as_json,
    available_to_allocate,
    create_allocation,
    customer_payload,
    default_return_url,
    get_existing_by_idempotency,
    get_settings,
    make_idempotency,
    provider_webhook_url,
    recalculate_session,
    resolve_online_account,
    resolve_physical_account,
    sync_allocation_from_gateway,
)


def _session_response(session):
    session.reload()
    return {
        "name": session.name,
        "status": session.status,
        "pos_system": session.pos_system,
        "pos_profile": session.pos_profile,
        "pos_station": session.pos_station,
        "pos_opening_shift": getattr(session, "pos_opening_shift", None),
        "business_date": getattr(session, "business_date", None),
        "cashier_user": session.owner,
        "customer": session.customer,
        "customer_name": session.customer_name,
        "mobile_number": session.mobile_number,
        "currency": session.currency,
        "grand_total": flt(session.grand_total, 3),
        "confirmed_paid_amount": flt(session.confirmed_paid_amount, 3),
        "pending_amount": flt(session.pending_amount, 3),
        "remaining_amount": flt(session.remaining_amount, 3),
        "invoice_doctype": session.invoice_doctype,
        "invoice_name": session.invoice_name,
        "finalized": bool(session.finalized),
        "draft_saved_at": session.draft_saved_at,
        "recover_until": getattr(session, "recover_until", None),
        "preflight_validated_at": getattr(session, "preflight_validated_at", None),
        "last_error": session.last_error,
        "completed_at": session.completed_at,
        "completed_by": session.completed_by,
        "allocations": allocation_rows(session.name),
    }


def _allocation_response(allocation):
    allocation.reload()
    return {
        "name": allocation.name,
        "session": allocation.session,
        "channel": allocation.channel,
        "status": allocation.status,
        "amount": flt(allocation.amount, 3),
        "currency": allocation.currency,
        "gateway_transaction": allocation.gateway_transaction,
        "provider": allocation.provider,
        "provider_account": allocation.provider_account,
        "actual_payment_method": allocation.actual_payment_method,
        "payment_terminal": allocation.payment_terminal,
        "terminal_id": allocation.terminal_id,
        "mobile_number": allocation.mobile_number,
        "payment_url": allocation.payment_url,
        "idempotency_key": allocation.idempotency_key,
        "expires_at": allocation.expires_at,
        "link_send_count": int(allocation.link_send_count or 0),
        "last_whatsapp_message": allocation.last_whatsapp_message,
        "cancelled_at": allocation.cancelled_at,
        "cancelled_reason": allocation.cancelled_reason,
    }


@frappe.whitelist()
def get_pos_payment_config(computer_name=None, pos_profile=None, branch=None):
    settings = get_settings()
    station = resolve_pos_station(
        computer_name=computer_name,
        pos_profile=pos_profile,
        branch=branch,
    )
    terminal = None
    terminal_capable = False
    physical_account = None

    try:
        if station and station.provider_account:
            physical_account = resolve_physical_account(
                frappe._dict({"pos_station": station.name}),
                station.provider_account,
            )
        elif getattr(settings, "physical_terminal_provider", None):
            physical_account = resolve_physical_account(
                frappe._dict({"pos_station": None}),
                settings.physical_terminal_provider,
            )

        if physical_account:
            if station and station.payment_terminal:
                terminal = frappe.get_doc("Payment Terminal", station.payment_terminal)
            else:
                terminal = resolve_payment_terminal(
                    provider_account=physical_account,
                    pos_profile=pos_profile,
                    branch=branch,
                )
            terminal_capable = hasattr(get_provider(physical_account), "create_terminal_payment")
    except Exception:
        pass

    return {
        "cash_mode_of_payment": getattr(settings, "cash_mode_of_payment", None) or "Cash",
        "physical_mode_of_payment": getattr(settings, "physical_mode_of_payment", None) or "Physical Payment Terminal",
        "electronic_mode_of_payment": getattr(settings, "electronic_mode_of_payment", None) or "Electronic Payment",
        "online_provider": getattr(settings, "online_provider", None) or getattr(settings, "default_provider", None),
        "physical_terminal_provider": getattr(settings, "physical_terminal_provider", None),
        "whatsapp_integration": getattr(settings, "whatsapp_integration", None) or "Frappe WhatsApp",
        "async_electronic_payment": bool(getattr(settings, "async_electronic_payment", 1)),
        "after_electronic_capture": getattr(settings, "after_electronic_capture", None) or "Mark Paid Only",
        "payment_link_expiry_minutes": int(getattr(settings, "payment_link_expiry_minutes", 30) or 30),
        "pending_sale_retention_hours": int(getattr(settings, "pending_sale_retention_hours", 24) or 24),
        "draft_a4_print_format": getattr(settings, "draft_a4_print_format", None),
        "draft_receipt_print_format": getattr(settings, "draft_receipt_print_format", None),
        "final_receipt_print_format": getattr(settings, "final_receipt_print_format", None),
        "default_draft_print_type": getattr(settings, "default_draft_print_type", None) or "Receipt",
        "station": station.name if station else None,
        "computer_name": station.computer_name if station else computer_name,
        "terminal": getattr(terminal, "name", None) if terminal else None,
        "terminal_id": getattr(terminal, "terminal_id", None) if terminal else None,
        "physical_terminal_api_ready": terminal_capable,
    }


@frappe.whitelist()
def create_pos_session(
    pos_system,
    company,
    grand_total,
    currency="KWD",
    customer=None,
    customer_name=None,
    mobile_number=None,
    email=None,
    pos_profile=None,
    branch=None,
    warehouse=None,
    computer_name=None,
    pos_station=None,
    pos_opening_shift=None,
    cart_reference=None,
    idempotency_key=None,
    draft_payload=None,
):
    grand_total = flt(grand_total, 3)
    if grand_total <= 0:
        frappe.throw("Grand Total must be greater than zero.")

    if idempotency_key:
        existing = get_existing_by_idempotency("POS Payment Session", idempotency_key)
        if existing:
            return _session_response(existing)

    station = None
    if pos_station:
        station = frappe.get_doc("POS Station", pos_station)
    else:
        station = resolve_pos_station(
            computer_name=computer_name,
            pos_profile=pos_profile,
            branch=branch,
        )
    if station:
        touch_pos_station(station)

    if customer and not customer_name:
        customer_name = frappe.db.get_value("Customer", customer, "customer_name") or customer

    doc = frappe.new_doc("POS Payment Session")
    doc.status = "Draft"
    doc.pos_system = pos_system or "API"
    doc.company = company
    doc.branch = branch or (getattr(station, "branch", None) if station else None)
    doc.pos_profile = pos_profile or (getattr(station, "pos_profile", None) if station else None)
    doc.pos_station = getattr(station, "name", None) if station else None
    doc.computer_name = getattr(station, "computer_name", None) if station else computer_name
    doc.warehouse = warehouse or (getattr(station, "warehouse", None) if station else None)
    doc.customer = customer
    doc.customer_name = customer_name
    doc.mobile_number = mobile_number
    doc.email = email
    doc.currency = currency or "KWD"
    doc.grand_total = grand_total
    doc.remaining_amount = grand_total
    doc.cart_reference = cart_reference
    doc.idempotency_key = idempotency_key or make_idempotency("SESSION", pos_system or "API")
    doc.draft_payload = as_json(draft_payload)
    apply_session_shift_context(doc, pos_opening_shift=pos_opening_shift, draft_payload=draft_payload)
    settings = get_settings()
    retention_hours = max(1, int(getattr(settings, "pending_sale_retention_hours", 24) or 24))
    doc.recover_until = add_to_date(now_datetime(), hours=retention_hours)
    doc.insert(ignore_permissions=True)
    recalculate_session(doc, publish=False)
    return _session_response(doc)


@frappe.whitelist()
def save_pos_draft(session_name, draft_payload, cart_reference=None):
    session = frappe.get_doc("POS Payment Session", session_name)
    assert_open_session(session)
    session.draft_payload = as_json(draft_payload)
    apply_session_shift_context(session, draft_payload=draft_payload)
    session.draft_saved_at = now_datetime()
    if cart_reference is not None:
        session.cart_reference = cart_reference
    session.save(ignore_permissions=True)
    return _session_response(session)


@frappe.whitelist()
def prepare_pos_invoice(session_name, invoice_payload=None, invoice_doctype="Sales Invoice", print_format=None):
    """Create/save the exact POS draft before any electronic money is requested.

    Saving the draft executes the site's normal Sales Invoice validation hooks
    (ERPNext plus any installed POS-specific/company controls). Payment Hub does
    not hardcode tax, negative-stock or selling-rate policy here.
    """
    session = frappe.get_doc("POS Payment Session", session_name)
    assert_open_session(session)

    from erpnext_payment_hub.pos.invoice import create_or_update_invoice

    try:
        doc, invoice_result = create_or_update_invoice(
            session,
            invoice_doctype=session.invoice_doctype or invoice_doctype or "Sales Invoice",
            invoice_name=session.invoice_name,
            invoice_payload=invoice_payload,
            submit=False,
            print_format=print_format,
        )
    except Exception as exc:
        # Preserve a cashier-friendly recovery note. The original exception is
        # re-raised so POSNext/POS Awesome still show the site's native validation.
        session.last_error = str(exc)[:2000]
        session.save(ignore_permissions=True)
        raise

    session.invoice_doctype = doc.doctype
    session.invoice_name = doc.name
    apply_session_shift_context(
        session,
        pos_opening_shift=doc.get("posa_pos_opening_shift") if hasattr(doc, "get") else None,
        draft_payload=invoice_payload or session.draft_payload,
    )
    session.draft_saved_at = session.draft_saved_at or now_datetime()
    session.preflight_validated_at = now_datetime()
    session.last_error = None
    session.save(ignore_permissions=True)

    return {
        "session": _session_response(session),
        "invoice": invoice_result,
        "preflight_validated": True,
    }


@frappe.whitelist()
def get_pos_session(session_name):
    return _session_response(frappe.get_doc("POS Payment Session", session_name))


@frappe.whitelist()
def add_cash_allocation(session_name, amount, mode_of_payment=None, idempotency_key=None):
    session = frappe.get_doc("POS Payment Session", session_name)
    assert_open_session(session)
    amount = assert_amount_available(session, amount)

    if idempotency_key:
        existing = get_existing_by_idempotency("POS Payment Allocation", idempotency_key)
        if existing:
            return {"allocation": _allocation_response(existing), "session": _session_response(session)}

    settings = get_settings()
    allocation = create_allocation(
        session,
        channel="Cash",
        amount=amount,
        mode_of_payment=mode_of_payment or getattr(settings, "cash_mode_of_payment", None) or "Cash",
        status="Captured",
        idempotency_key=idempotency_key,
    )
    allocation.captured_at = now_datetime()
    allocation.save(ignore_permissions=True)
    recalculate_session(session)
    return {"allocation": _allocation_response(allocation), "session": _session_response(session)}


@frappe.whitelist()
def create_payment_link(
    session_name,
    amount,
    mobile_number=None,
    provider_account=None,
    payment_method="ALL",
    mode_of_payment=None,
    idempotency_key=None,
):
    session = frappe.get_doc("POS Payment Session", session_name)
    assert_open_session(session)

    if idempotency_key:
        existing = get_existing_by_idempotency("POS Payment Allocation", idempotency_key)
        if existing:
            return {"allocation": _allocation_response(existing), "session": _session_response(session)}

    amount = assert_amount_available(session, amount)
    account = resolve_online_account(provider_account)
    provider = get_provider(account)
    customer = customer_payload(session, mobile_number)
    if not customer.get("phone"):
        frappe.throw("Customer mobile number is required for Electronic Payment.")

    settings = get_settings()
    allocation = create_allocation(
        session,
        channel="Electronic Payment",
        amount=amount,
        mode_of_payment=mode_of_payment or getattr(settings, "electronic_mode_of_payment", None) or "Electronic Payment",
        status="Draft",
        idempotency_key=idempotency_key,
        mobile_number=f"+{customer['phone_country_code']}{customer['phone']}",
        provider_account=account.name,
        provider=account.provider,
    )

    try:
        normalized = provider.create_payment(
            amount=amount,
            currency=session.currency,
            reference_doctype="POS Payment Session",
            reference_name=session.name,
            payment_method=payment_method,
            customer=customer,
            return_url=default_return_url(),
            cancel_url=default_return_url(),
            webhook_url=provider_webhook_url(account),
            terminal=None,
            pos_context=frappe._dict({
                "company": session.company,
                "branch": session.branch,
                "pos_profile": session.pos_profile,
                "warehouse": session.warehouse,
            }),
        )

        txn = create_gateway_transaction(
            transaction_type="Payment",
            provider_account=account,
            reference_doctype="POS Payment Session",
            reference_name=session.name,
            payment_method=payment_method,
            amount=amount,
            currency=session.currency,
            normalized=normalized,
            branch=session.branch,
            pos_profile=session.pos_profile,
            pos_station=session.pos_station,
            computer_name=session.computer_name,
            pos_payment_session=session.name,
            pos_payment_allocation=allocation.name,
        )
        allocation.gateway_transaction = txn.name
        allocation.payment_url = txn.payment_url
        allocation.actual_payment_method = txn.provider_payment_type
        allocation.status = "Captured" if txn.status == "Captured" else "Waiting"
        expiry_minutes = max(1, int(getattr(settings, "payment_link_expiry_minutes", 30) or 30))
        allocation.expires_at = add_to_date(now_datetime(), minutes=expiry_minutes)
        if allocation.status == "Captured":
            allocation.captured_at = now_datetime()
        allocation.save(ignore_permissions=True)
        recalculate_session(session)
    except Exception as exc:
        allocation.status = "Failed"
        allocation.failed_reason = str(exc)[:2000] or frappe.get_traceback()[-2000:]
        allocation.save(ignore_permissions=True)
        session.last_error = str(exc)[:2000]
        session.save(ignore_permissions=True)
        recalculate_session(session)
        raise

    return {
        "allocation": _allocation_response(allocation),
        "session": _session_response(session),
        "payment_message": compose_payment_message(allocation.name),
    }


@frappe.whitelist()
def begin_async_electronic_sale(
    pos_system,
    company,
    grand_total,
    electronic_amount,
    mobile_number,
    draft_payload,
    currency="KWD",
    cash_amount=0,
    customer=None,
    customer_name=None,
    email=None,
    pos_profile=None,
    branch=None,
    warehouse=None,
    computer_name=None,
    pos_station=None,
    pos_opening_shift=None,
    cart_reference=None,
    provider_account=None,
    payment_method="KNET",
    cash_mode_of_payment=None,
    electronic_mode_of_payment=None,
    idempotency_key=None,
    send_whatsapp=1,
):
    """Create an asynchronous POS electronic-payment sale in one idempotent call.

    This is the common frontend adapter entry point for POSNext / POS Awesome.
    Provider-specific behavior remains inside Payment Hub.
    """
    grand_total = flt(grand_total, 3)
    electronic_amount = flt(electronic_amount, 3)
    cash_amount = flt(cash_amount, 3)

    if grand_total <= 0:
        frappe.throw("Grand Total must be greater than zero.")
    if electronic_amount <= 0:
        frappe.throw("Electronic Payment amount must be greater than zero.")
    if cash_amount < 0:
        frappe.throw("Cash amount cannot be negative.")
    if abs((cash_amount + electronic_amount) - grand_total) > 0.001:
        frappe.throw(
            f"Cash + Electronic Payment must equal the sale total. "
            f"Allocated {cash_amount + electronic_amount:.3f} / {grand_total:.3f} {currency}."
        )

    base_key = idempotency_key or cart_reference or make_idempotency("POS", pos_system or "API")
    session = create_pos_session(
        pos_system=pos_system,
        company=company,
        grand_total=grand_total,
        currency=currency,
        customer=customer,
        customer_name=customer_name,
        mobile_number=mobile_number,
        email=email,
        pos_profile=pos_profile,
        branch=branch,
        warehouse=warehouse,
        computer_name=computer_name,
        pos_station=pos_station,
        pos_opening_shift=pos_opening_shift,
        cart_reference=cart_reference,
        idempotency_key=f"{base_key}:SESSION",
        draft_payload=draft_payload,
    )
    session_name = session["name"]

    # Payment safety: validate and persist the exact Sales Invoice draft BEFORE
    # creating a provider payment request. If the site's normal invoice
    # validation rejects the cart, no gateway transaction/link is created.
    prepare_pos_invoice(
        session_name=session_name,
        invoice_payload=draft_payload,
        invoice_doctype="Sales Invoice",
    )

    electronic = create_payment_link(
        session_name=session_name,
        amount=electronic_amount,
        mobile_number=mobile_number,
        provider_account=provider_account,
        payment_method=payment_method or "KNET",
        mode_of_payment=electronic_mode_of_payment,
        idempotency_key=f"{base_key}:ELECTRONIC",
    )

    if cash_amount > 0:
        add_cash_allocation(
            session_name=session_name,
            amount=cash_amount,
            mode_of_payment=cash_mode_of_payment,
            idempotency_key=f"{base_key}:CASH",
        )

    allocation = electronic["allocation"]
    send_result = None
    if cint(send_whatsapp) and allocation.get("status") != "Captured":
        # Do not duplicate a WhatsApp message when a browser retries the same
        # request after the server already completed the send.
        if int(allocation.get("link_send_count") or 0) == 0:
            send_result = send_payment_link(allocation["name"])
        else:
            send_result = {
                "allocation": allocation,
                "session": get_pos_session(session_name),
                "already_sent": True,
            }

    return {
        "session": get_pos_session(session_name),
        "electronic_allocation": _allocation_response(
            frappe.get_doc("POS Payment Allocation", allocation["name"])
        ),
        "payment_message": compose_payment_message(allocation["name"]),
        "whatsapp": send_result,
    }


@frappe.whitelist()
def compose_payment_message(allocation_name):
    allocation = frappe.get_doc("POS Payment Allocation", allocation_name)
    session = frappe.get_doc("POS Payment Session", allocation.session)
    if not allocation.payment_url:
        frappe.throw("Payment URL is not available for this allocation.")
    template = (
        getattr(get_settings(), "payment_link_message_template", None)
        or "Payment request {session}\nAmount: {currency} {amount}\nPlease complete your payment using the secure link below:\n{payment_url}"
    )
    return template.format(
        session=session.name,
        currency=session.currency,
        amount=f"{flt(allocation.amount, 3):.3f}",
        payment_url=allocation.payment_url,
        customer=session.customer_name or session.customer or "Customer",
        mobile=allocation.mobile_number or session.mobile_number or "",
    )


@frappe.whitelist()
def mark_payment_link_sent(allocation_name, sent_via=None, message_name=None):
    allocation = frappe.get_doc("POS Payment Allocation", allocation_name)
    allocation.link_sent_at = now_datetime()
    allocation.link_sent_via = sent_via or (getattr(get_settings(), "whatsapp_integration", None) or "Frappe WhatsApp")
    allocation.link_send_count = int(allocation.link_send_count or 0) + 1
    if message_name:
        allocation.last_whatsapp_message = message_name
    allocation.save(ignore_permissions=True)
    return _allocation_response(allocation)


@frappe.whitelist()
def send_payment_link(allocation_name):
    allocation = frappe.get_doc("POS Payment Allocation", allocation_name)
    session = frappe.get_doc("POS Payment Session", allocation.session)
    if allocation.channel != "Electronic Payment":
        frappe.throw("Only Electronic Payment allocations can send a payment link.")
    if allocation.status == "Captured":
        frappe.throw("This payment is already captured.")
    if allocation.status != "Waiting":
        frappe.throw(
            f"This payment attempt is {allocation.status}. "
            "Create a new payment link instead of resending the old link."
        )
    if not allocation.mobile_number:
        frappe.throw("Mobile number is missing for this payment allocation.")
    message = compose_payment_message(allocation.name)
    from erpnext_payment_hub.pos.whatsapp import send_payment_message
    result = send_payment_message(allocation=allocation, session=session, message=message)
    message_name = result.get("message_name") if isinstance(result, dict) else None
    mark_payment_link_sent(
        allocation.name,
        sent_via=(result.get("integration") if isinstance(result, dict) else None),
        message_name=message_name,
    )
    allocation.reload()
    return {
        "allocation": _allocation_response(allocation),
        "session": _session_response(session),
        "message": message,
        "send_result": result,
    }


@frappe.whitelist()
def resend_payment_link(allocation_name):
    return send_payment_link(allocation_name)


@frappe.whitelist()
def create_new_payment_link(
    session_name,
    amount=None,
    mobile_number=None,
    provider_account=None,
    payment_method=None,
    mode_of_payment=None,
    send_whatsapp=1,
):
    """Create a fresh gateway attempt for the still-unpaid balance.

    Failed/expired attempts are preserved for audit. A second link is refused
    while any earlier provider transaction is still remotely Pending, which
    prevents two simultaneously-payable links for the same balance.
    """
    session = frappe.get_doc("POS Payment Session", session_name)
    assert_open_session(session)
    session = recalculate_session(session)

    if flt(session.confirmed_paid_amount, 3) >= flt(session.grand_total, 3):
        frappe.throw("This sale is already fully paid. Complete the existing invoice instead.")

    electronic_rows = [
        row for row in allocation_rows(session.name)
        if row.channel == "Electronic Payment"
    ]
    previous = electronic_rows[-1] if electronic_rows else None

    # Recheck every still-active electronic attempt before opening another
    # payable URL. This is intentionally fail-closed: two remotely Pending links
    # must never exist for the same unpaid balance.
    for old_row in electronic_rows:
        if old_row.status not in ("Waiting", "Expired", "Failed") or not old_row.gateway_transaction:
            continue
        txn = frappe.get_doc("Gateway Transaction", old_row.gateway_transaction)
        if txn.status == "Pending" or old_row.status == "Waiting":
            try:
                check_pos_payment(old_row.name)
                old_row = frappe.get_doc("POS Payment Allocation", old_row.name)
                txn = frappe.get_doc("Gateway Transaction", old_row.gateway_transaction)
            except Exception:
                frappe.throw(
                    "Unable to confirm a previous payment attempt is closed. "
                    "Check its status before creating another link."
                )
        if txn.status == "Pending" or old_row.status == "Waiting":
            frappe.throw(
                f"Previous payment attempt {old_row.name} is still Pending. "
                "A new link cannot be created until it is Failed/Expired or Captured."
            )
        if txn.status == "Captured" or old_row.status == "Captured":
            recalculate_session(session)
            frappe.throw(f"Previous payment attempt {old_row.name} has already been captured.")

    available = flt(available_to_allocate(session), 3)
    requested = flt(amount, 3) if amount not in (None, "") else available
    if requested <= 0:
        frappe.throw("There is no unpaid balance available for a new payment link.")
    if requested > available:
        frappe.throw(f"Maximum unpaid balance is {available:.3f} {session.currency}.")

    previous_txn = None
    if previous and previous.gateway_transaction:
        previous_txn = frappe.get_doc("Gateway Transaction", previous.gateway_transaction)

    result = create_payment_link(
        session_name=session.name,
        amount=requested,
        mobile_number=mobile_number or (previous.mobile_number if previous else None) or session.mobile_number,
        provider_account=provider_account or (previous.provider_account if previous else None),
        payment_method=payment_method
        or (previous_txn.payment_method if previous_txn else None)
        or "KNET",
        mode_of_payment=mode_of_payment or (previous.mode_of_payment if previous else None),
        idempotency_key=make_idempotency("RETRY", session.name),
    )

    allocation = result["allocation"]
    session.reload()
    session.last_error = None
    session.save(ignore_permissions=True)
    whatsapp = None
    if cint(send_whatsapp) and allocation.get("status") == "Waiting":
        whatsapp = send_payment_link(allocation["name"])

    return {
        "session": get_pos_session(session.name),
        "electronic_allocation": _allocation_response(
            frappe.get_doc("POS Payment Allocation", allocation["name"])
        ),
        "payment_message": compose_payment_message(allocation["name"]),
        "whatsapp": whatsapp,
        "previous_allocation": previous.name if previous else None,
    }


@frappe.whitelist()
def get_pos_draft_print(session_name, print_format=None, print_type=None):
    """Return the printable draft for an unpaid/partially-paid POS session."""
    session = frappe.get_doc("POS Payment Session", session_name)
    from erpnext_payment_hub.pos.invoice import build_print_result, resolve_draft_print_format

    resolved_print_format, resolved_print_type = resolve_draft_print_format(
        print_format=print_format, print_type=print_type
    )
    if session.finalized:
        frappe.throw("This POS Payment Session is already completed.")

    payment_state = (
        "Partially Paid" if flt(session.confirmed_paid_amount, 3) > 0 else "Unpaid"
    )

    if not session.invoice_name:
        prepared = prepare_pos_invoice(
            session_name=session.name,
            invoice_payload=session.draft_payload,
            invoice_doctype=session.invoice_doctype or "Sales Invoice",
            print_format=resolved_print_format,
        )
        return {
            **prepared,
            "print_type": resolved_print_type,
            "payment_state": payment_state,
        }

    doc = frappe.get_doc(session.invoice_doctype or "Sales Invoice", session.invoice_name)
    return {
        "session": _session_response(session),
        "invoice": build_print_result(doc, print_format=resolved_print_format),
        "print_type": resolved_print_type,
        "payment_state": payment_state,
    }


@frappe.whitelist()
def check_pos_payment(allocation_name):
    allocation = frappe.get_doc("POS Payment Allocation", allocation_name)
    if not allocation.gateway_transaction:
        frappe.throw("This allocation has no Gateway Transaction.")
    txn = frappe.get_doc("Gateway Transaction", allocation.gateway_transaction)
    account = frappe.get_doc("Payment Provider Account", txn.provider_account)
    provider = get_provider(account)
    normalized = provider.get_payment_status(txn)
    update_transaction_from_status(txn, normalized)
    allocation.reload()
    return {"allocation": _allocation_response(allocation), "session": _session_response(frappe.get_doc("POS Payment Session", allocation.session))}


@frappe.whitelist()
def start_terminal_payment(
    session_name,
    amount,
    provider_account=None,
    payment_method="CARD",
    mode_of_payment=None,
    computer_name=None,
    idempotency_key=None,
):
    session = frappe.get_doc("POS Payment Session", session_name)
    assert_open_session(session)
    amount = assert_amount_available(session, amount)

    if idempotency_key:
        existing = get_existing_by_idempotency("POS Payment Allocation", idempotency_key)
        if existing:
            return {"allocation": _allocation_response(existing), "session": _session_response(session)}

    account = resolve_physical_account(session, provider_account)
    provider = get_provider(account)
    if not hasattr(provider, "create_terminal_payment"):
        frappe.throw(
            f"{account.provider} physical-terminal API is not implemented in Payment Hub yet. "
            "The POS session backend is ready; add the provider SmartPOS/ECR adapter next."
        )

    station = frappe.get_doc("POS Station", session.pos_station) if session.pos_station else resolve_pos_station(
        computer_name=computer_name or session.computer_name,
        pos_profile=session.pos_profile,
        branch=session.branch,
    )
    terminal = None
    if station and station.payment_terminal:
        terminal = frappe.get_doc("Payment Terminal", station.payment_terminal)
    if not terminal:
        terminal = resolve_payment_terminal(
            provider_account=account,
            pos_profile=session.pos_profile,
            branch=session.branch,
            company=session.company,
            warehouse=session.warehouse,
        )
    if not terminal:
        frappe.throw("No Payment Terminal is mapped to this POS Station/Profile.")

    settings = get_settings()
    allocation = create_allocation(
        session,
        channel="Physical Payment Terminal",
        amount=amount,
        mode_of_payment=mode_of_payment or getattr(settings, "physical_mode_of_payment", None) or "Physical Payment Terminal",
        status="Draft",
        idempotency_key=idempotency_key,
        provider_account=account.name,
        provider=account.provider,
        payment_terminal=getattr(terminal, "name", None),
        terminal_id=getattr(terminal, "terminal_id", None),
    )

    normalized = provider.create_terminal_payment(
        amount=amount,
        currency=session.currency,
        reference=session.name,
        payment_method=payment_method,
        terminal=terminal,
        pos_station=station,
    )
    txn = create_gateway_transaction(
        transaction_type="Payment",
        provider_account=account,
        reference_doctype="POS Payment Session",
        reference_name=session.name,
        payment_method=payment_method,
        amount=amount,
        currency=session.currency,
        normalized=normalized,
        branch=session.branch,
        pos_profile=session.pos_profile,
        payment_terminal=getattr(terminal, "name", None),
        terminal_id=getattr(terminal, "terminal_id", None),
        pos_station=getattr(station, "name", None) if station else session.pos_station,
        computer_name=getattr(station, "computer_name", None) if station else session.computer_name,
        pos_payment_session=session.name,
        pos_payment_allocation=allocation.name,
    )
    allocation.gateway_transaction = txn.name
    allocation.status = "Captured" if txn.status == "Captured" else "Waiting"
    allocation.actual_payment_method = txn.provider_payment_type
    if allocation.status == "Captured":
        allocation.captured_at = now_datetime()
    allocation.save(ignore_permissions=True)
    recalculate_session(session)
    return {"allocation": _allocation_response(allocation), "session": _session_response(session)}


@frappe.whitelist()
def complete_pos_session(
    session_name,
    invoice_doctype="Sales Invoice",
    invoice_name=None,
    invoice_payload=None,
    submit=1,
    print_format=None,
):
    submit_requested = bool(cint(submit)) if isinstance(submit, (str, int, bool)) else bool(submit)
    session = frappe.get_doc("POS Payment Session", session_name)

    # v0.2.1 could mark a session Completed even when submit=0 created only a draft
    # invoice. Repair that state lazily so the same draft can be safely submitted.
    if session.finalized:
        if not session.invoice_doctype or not session.invoice_name:
            frappe.throw(f"Session {session.name} is finalized but invoice reference is missing.")
        from erpnext_payment_hub.pos.invoice import build_print_result
        existing_doc = frappe.get_doc(session.invoice_doctype, session.invoice_name)
        if existing_doc.docstatus == 1:
            session.status = "Completed"
            session.save(ignore_permissions=True)
            return {
                "session": _session_response(session),
                "invoice": build_print_result(existing_doc, print_format),
            }
        if existing_doc.docstatus == 2:
            frappe.throw(
                f"Final invoice {existing_doc.doctype} {existing_doc.name} is cancelled. "
                "Create or select a valid invoice before completing this session."
            )
        session.finalized = 0
        session.completed_at = None
        session.completed_by = None
        session.save(ignore_permissions=True)

    session = recalculate_session(session)
    if session.status != "Ready to Complete" or flt(session.confirmed_paid_amount, 3) < flt(session.grand_total, 3):
        frappe.throw(
            f"Session {session.name} is not Ready to Complete. "
            f"Confirmed {flt(session.confirmed_paid_amount,3):.3f} / {flt(session.grand_total,3):.3f} {session.currency}."
        )

    # Reuse a draft invoice already created for this session. This makes draft
    # creation idempotent and prevents a second invoice on the submit call.
    effective_invoice_name = invoice_name or session.invoice_name
    if effective_invoice_name and not invoice_name and session.invoice_doctype:
        effective_invoice_doctype = session.invoice_doctype
    else:
        effective_invoice_doctype = invoice_doctype or "Sales Invoice"

    from erpnext_payment_hub.pos.invoice import create_or_update_invoice
    try:
        doc, invoice_result = create_or_update_invoice(
            session,
            invoice_doctype=effective_invoice_doctype,
            invoice_name=effective_invoice_name,
            invoice_payload=invoice_payload,
            submit=submit_requested,
            print_format=print_format,
        )
    except Exception as exc:
        # Money may already be captured. Preserve the useful native ERPNext/POS
        # validation message and keep the session recoverable for retry.
        session.last_error = str(exc)[:2000]
        session.save(ignore_permissions=True)
        raise

    session.invoice_doctype = doc.doctype
    session.invoice_name = doc.name
    session.last_error = None

    if doc.docstatus == 1:
        session.finalized = 1
        session.status = "Completed"
        session.completed_at = session.completed_at or now_datetime()
        session.completed_by = session.completed_by or frappe.session.user
    else:
        # A saved draft is not a completed sale. Keep the fully paid session in
        # Ready to Complete so the cashier can resume and submit the same invoice.
        session.finalized = 0
        session.status = "Ready to Complete"
        session.completed_at = None
        session.completed_by = None
    session.save(ignore_permissions=True)

    # Gateway references become accounting references only after the invoice is
    # submitted. Until then they remain tied to the POS Payment Session.
    if doc.docstatus == 1:
        txns = frappe.get_all(
            "Gateway Transaction",
            filters={"pos_payment_session": session.name, "transaction_type": "Payment"},
            pluck="name",
        )
        for name in txns:
            txn = frappe.get_doc("Gateway Transaction", name)
            txn.reference_doctype = doc.doctype
            txn.reference_name = doc.name
            txn.save(ignore_permissions=True)

    return {"session": _session_response(session), "invoice": invoice_result}


@frappe.whitelist()
def finalize_pos_session(session_name, invoice_doctype="Sales Invoice", invoice_name=None, invoice_payload=None, submit=1, print_format=None):
    """Backward-compatible alias for v0.2.0 callers."""
    return complete_pos_session(
        session_name=session_name,
        invoice_doctype=invoice_doctype,
        invoice_name=invoice_name,
        invoice_payload=invoice_payload,
        submit=submit,
        print_format=print_format,
    )


@frappe.whitelist()
def cancel_pos_payment(allocation_name, reason=None):
    allocation = frappe.get_doc("POS Payment Allocation", allocation_name)
    if allocation.status == "Captured":
        frappe.throw("Captured payments cannot be cancelled; use the refund flow.")
    session = frappe.get_doc("POS Payment Session", allocation.session)
    allocation.status = "Cancelled"
    allocation.cancelled_at = now_datetime()
    allocation.cancelled_reason = reason or "Cancelled by cashier"
    allocation.save(ignore_permissions=True)
    recalculate_session(session)
    return {
        "allocation": _allocation_response(allocation),
        "session": _session_response(session),
        "remote_cancelled": False,
        "warning": "Local allocation cancelled. If the provider payment link remains valid and later captures, the webhook will restore it to Captured.",
    }


@frappe.whitelist()
def cancel_pos_session(session_name, reason=None):
    session = frappe.get_doc("POS Payment Session", session_name)
    recalculate_session(session)
    if flt(session.confirmed_paid_amount, 3) > 0:
        frappe.throw("This session already contains captured money. Refund captured allocations before cancelling the sale.")
    for row in allocation_rows(session.name):
        if row.status in ("Draft", "Waiting", "Failed", "Expired"):
            doc = frappe.get_doc("POS Payment Allocation", row.name)
            doc.status = "Cancelled"
            doc.cancelled_at = now_datetime()
            doc.cancelled_reason = reason or "Sale cancelled by cashier"
            doc.save(ignore_permissions=True)
    session.status = "Cancelled"
    session.last_error = reason or "Sale cancelled by cashier"
    session.save(ignore_permissions=True)
    return _session_response(session)


@frappe.whitelist()
def check_session_payments(session_name):
    session = frappe.get_doc("POS Payment Session", session_name)
    checked = []
    for row in allocation_rows(session.name):
        if row.status == "Waiting" and row.gateway_transaction:
            try:
                result = check_pos_payment(row.name)
                checked.append({"allocation": row.name, "ok": True, "status": result["allocation"]["status"]})
            except Exception as exc:
                checked.append({"allocation": row.name, "ok": False, "error": str(exc)})
    return {"checked": checked, "session": _session_response(frappe.get_doc("POS Payment Session", session.name))}


@frappe.whitelist()
def get_payment_hub_pos_context(
    current_pos_profile=None,
    current_pos_opening_shift=None,
    selected_pos_profile=None,
):
    """Return cashier/admin profile and shift defaults for the Payment Hub POS dialog."""
    return build_scope_context(
        current_pos_profile=current_pos_profile,
        current_pos_opening_shift=current_pos_opening_shift,
        selected_pos_profile=selected_pos_profile,
    )


@frappe.whitelist()
def get_pending_payments(pos_profile=None, pos_station=None, limit=50):
    filters = {"status": ["in", list(PENDING_SESSION_STATUSES)]}
    if pos_profile:
        filters["pos_profile"] = pos_profile
    if pos_station:
        filters["pos_station"] = pos_station
    return frappe.get_all(
        "POS Payment Session",
        filters=filters,
        fields=["name", "status", "pos_system", "pos_profile", "pos_station", "customer", "customer_name", "mobile_number", "currency", "grand_total", "confirmed_paid_amount", "pending_amount", "remaining_amount", "creation", "modified"],
        order_by="modified desc",
        limit=int(limit or 50),
    )


@frappe.whitelist()
def get_paid_pending_sales(pos_profile=None, pos_station=None, limit=50):
    filters = {"status": ["in", list(PAID_PENDING_SESSION_STATUSES)], "finalized": 0}
    if pos_profile:
        filters["pos_profile"] = pos_profile
    if pos_station:
        filters["pos_station"] = pos_station
    return frappe.get_all(
        "POS Payment Session",
        filters=filters,
        fields=["name", "status", "pos_system", "pos_profile", "pos_station", "customer", "customer_name", "mobile_number", "currency", "grand_total", "confirmed_paid_amount", "paid_at", "creation", "modified"],
        order_by="paid_at desc, modified desc",
        limit=int(limit or 50),
    )



def _queue_sessions(
    queue="Waiting",
    pos_profile=None,
    pos_station=None,
    search=None,
    limit=50,
    pos_opening_shift=None,
    from_date=None,
    to_date=None,
    current_pos_profile=None,
):
    queue = (queue or "Waiting").strip().title()
    pos_profile = effective_pos_profile(pos_profile, current_pos_profile)
    validate_shift_scope(pos_opening_shift=pos_opening_shift, pos_profile=pos_profile)

    filters = {}
    if pos_profile:
        filters["pos_profile"] = pos_profile
    if pos_station:
        filters["pos_station"] = pos_station

    if queue == "Waiting":
        # Waiting is intentionally profile-scoped, not shift/date-scoped. A link
        # created near shift close must remain visible to the next cashier until
        # the recovery window expires or the payment is resolved.
        filters["status"] = ["in", ["Draft", *list(PENDING_SESSION_STATUSES)]]
        filters["recover_until"] = [">=", now_datetime()]
    elif queue in ("Paid", "Ready"):
        filters["status"] = ["in", list(PAID_PENDING_SESSION_STATUSES)]
    elif queue in ("Failed", "Expired", "Cancelled"):
        if queue == "Failed":
            attempts = frappe.get_all(
                "POS Payment Allocation",
                filters={"channel": "Electronic Payment"},
                fields=["session", "status", "sequence", "creation"],
                order_by="session asc, sequence desc, creation desc",
                limit=5000,
            )
            latest = {}
            for attempt in attempts:
                if attempt.session not in latest:
                    latest[attempt.session] = attempt
            allocation_sessions = [
                name for name, attempt in latest.items()
                if attempt.status in ("Failed", "Expired")
            ]
            filters["name"] = ["in", allocation_sessions or ["__none__"]]
            filters["status"] = ["!=", "Completed"]
        else:
            filters["status"] = queue
    elif queue == "Completed":
        filters["status"] = "Completed"

    # Paid/Failed/history-style queues default to a shift. Date ranges are used
    # only when the user explicitly selects Today/Yesterday/Custom.
    if queue != "Waiting":
        if pos_opening_shift:
            filters["pos_opening_shift"] = pos_opening_shift
        elif from_date and to_date:
            filters["business_date"] = ["between", [from_date, to_date]]
        elif from_date:
            filters["business_date"] = [">=", from_date]
        elif to_date:
            filters["business_date"] = ["<=", to_date]

    or_filters = None
    if search:
        like = f"%{search}%"
        or_filters = {
            "name": ["like", like],
            "customer": ["like", like],
            "customer_name": ["like", like],
            "mobile_number": ["like", like],
            "cart_reference": ["like", like],
            "invoice_name": ["like", like],
        }

    return frappe.get_all(
        "POS Payment Session",
        filters=filters,
        or_filters=or_filters,
        fields=[
            "name", "status", "pos_system", "pos_profile", "pos_station",
            "pos_opening_shift", "business_date", "owner",
            "customer", "customer_name", "mobile_number", "currency",
            "grand_total", "confirmed_paid_amount", "pending_amount",
            "remaining_amount", "cart_reference", "invoice_doctype", "invoice_name",
            "last_error", "recover_until", "preflight_validated_at",
            "paid_at", "completed_at", "creation", "modified",
        ],
        order_by="modified desc",
        limit=int(limit or 50),
    )


@frappe.whitelist()
def get_sales_queue(
    queue="Waiting",
    pos_profile=None,
    pos_station=None,
    search=None,
    limit=50,
    pos_opening_shift=None,
    from_date=None,
    to_date=None,
    current_pos_profile=None,
    current_pos_opening_shift=None,
):
    rows = _queue_sessions(
        queue, pos_profile, pos_station, search, limit,
        pos_opening_shift=pos_opening_shift,
        from_date=from_date,
        to_date=to_date,
        current_pos_profile=current_pos_profile,
    )
    session_names = [row.name for row in rows]
    latest_electronic = {}
    if session_names:
        allocations = frappe.get_all(
            "POS Payment Allocation",
            filters={
                "session": ["in", session_names],
                "channel": "Electronic Payment",
            },
            fields=[
                "name", "session", "sequence", "status", "link_send_count", "link_sent_at",
                "last_whatsapp_message", "gateway_transaction", "provider",
                "actual_payment_method", "payment_url", "modified",
            ],
            order_by="session asc, sequence desc, creation desc",
        )
        for allocation in allocations:
            if allocation.session not in latest_electronic:
                latest_electronic[allocation.session] = allocation

    for row in rows:
        allocation = latest_electronic.get(row.name)
        row["cashier_user"] = row.owner
        row["is_previous_shift"] = bool(
            current_pos_opening_shift
            and row.pos_opening_shift
            and row.pos_opening_shift != current_pos_opening_shift
        )
        row["electronic_allocation"] = allocation.name if allocation else None
        row["electronic_status"] = allocation.status if allocation else None
        row["link_send_count"] = int(allocation.link_send_count or 0) if allocation else 0
        row["link_sent_at"] = allocation.link_sent_at if allocation else None
        row["last_whatsapp_message"] = allocation.last_whatsapp_message if allocation else None
        row["gateway_transaction"] = allocation.gateway_transaction if allocation else None
        row["provider"] = allocation.provider if allocation else None
        row["actual_payment_method"] = allocation.actual_payment_method if allocation else None
        row["whatsapp_status"] = None
        if allocation and allocation.last_whatsapp_message and frappe.db.exists("DocType", "WhatsApp Message"):
            row["whatsapp_status"] = frappe.db.get_value(
                "WhatsApp Message", allocation.last_whatsapp_message, "status"
            )

    return {
        "queue": queue,
        "rows": rows,
        "count": len(rows),
    }


@frappe.whitelist()
def get_sales_queue_counts(
    pos_profile=None,
    pos_station=None,
    pos_opening_shift=None,
    from_date=None,
    to_date=None,
    current_pos_profile=None,
    current_pos_opening_shift=None,
):
    common = dict(
        pos_profile=pos_profile,
        pos_station=pos_station,
        pos_opening_shift=pos_opening_shift,
        from_date=from_date,
        to_date=to_date,
        current_pos_profile=current_pos_profile,
    )
    return {
        "waiting": len(_queue_sessions("Waiting", limit=500, **common)),
        "paid": len(_queue_sessions("Paid", limit=500, **common)),
        "failed": len(_queue_sessions("Failed", limit=500, **common)),
    }

from __future__ import annotations

import json
import uuid

import frappe
from frappe.utils import add_to_date, flt, now_datetime

from erpnext_payment_hub.gateway import (
    _normalize_kuwait_phone,
    get_provider,
    get_provider_account,
    resolve_payment_terminal,
    resolve_pos_station,
    update_transaction_from_status,
)

OPEN_ALLOCATION_STATUSES = ("Draft", "Waiting", "Captured")
PENDING_SESSION_STATUSES = ("Payment Pending", "Partially Paid")
PAID_PENDING_SESSION_STATUSES = ("Paid", "Ready to Complete")
FAILED_SESSION_STATUSES = ("Failed", "Expired", "Cancelled")


def as_json(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def get_settings():
    return frappe.get_single("Payment Hub Settings")


def allocation_rows(session_name):
    return frappe.get_all(
        "POS Payment Allocation",
        filters={"session": session_name},
        fields=[
            "name", "sequence", "channel", "status", "mode_of_payment", "amount",
            "currency", "gateway_transaction", "provider_account", "provider",
            "actual_payment_method", "payment_terminal", "terminal_id", "mobile_number",
            "payment_url", "idempotency_key", "link_sent_at", "link_sent_via",
            "expires_at", "link_send_count", "last_whatsapp_message",
            "captured_at", "cancelled_at", "cancelled_reason", "failed_reason", "creation", "modified",
        ],
        order_by="sequence asc, creation asc",
    )


def _publish_session(session, event="payment_hub_pos_update"):
    try:
        frappe.publish_realtime(
            event,
            {
                "session": session.name,
                "status": session.status,
                "grand_total": flt(session.grand_total, 3),
                "confirmed_paid_amount": flt(session.confirmed_paid_amount, 3),
                "pending_amount": flt(session.pending_amount, 3),
                "remaining_amount": flt(session.remaining_amount, 3),
            },
            user=session.owner,
            after_commit=True,
        )
    except Exception:
        pass


def recalculate_session(session_or_name, *, publish=True):
    session = (
        frappe.get_doc("POS Payment Session", session_or_name)
        if isinstance(session_or_name, str)
        else session_or_name
    )
    if session.finalized:
        session.status = "Completed"
        session.save(ignore_permissions=True)
        return session

    rows = allocation_rows(session.name)
    confirmed = sum(flt(r.amount, 3) for r in rows if r.status == "Captured")
    pending = sum(flt(r.amount, 3) for r in rows if r.status == "Waiting")
    total = flt(session.grand_total, 3)
    remaining = max(total - confirmed - pending, 0)

    old_status = session.status
    session.confirmed_paid_amount = flt(confirmed, 3)
    session.pending_amount = flt(pending, 3)
    session.remaining_amount = flt(remaining, 3)
    session.last_status_check = now_datetime()

    terminal_statuses = [r.status for r in rows]
    if confirmed >= total and total > 0:
        session.status = "Ready to Complete"
        if not session.paid_at:
            session.paid_at = now_datetime()
    elif confirmed > 0:
        session.status = "Partially Paid"
    elif pending > 0:
        session.status = "Payment Pending"
    elif terminal_statuses and all(x in ("Failed", "Cancelled", "Expired") for x in terminal_statuses):
        if any(x == "Failed" for x in terminal_statuses):
            session.status = "Failed"
        elif any(x == "Expired" for x in terminal_statuses):
            session.status = "Expired"
        else:
            session.status = "Cancelled"
    elif session.status not in ("Failed", "Expired", "Cancelled"):
        session.status = "Draft"

    session.save(ignore_permissions=True)
    if publish and old_status != session.status:
        _publish_session(session)
    return session


def allocated_amount(session_name):
    rows = allocation_rows(session_name)
    return sum(
        flt(r.amount, 3)
        for r in rows
        if r.status not in ("Failed", "Cancelled", "Expired")
    )


def available_to_allocate(session):
    return max(flt(session.grand_total, 3) - flt(allocated_amount(session.name), 3), 0)


def assert_open_session(session):
    if session.finalized or session.status == "Completed":
        frappe.throw(f"POS Payment Session {session.name} is already completed.")
    if session.status in ("Cancelled", "Expired"):
        frappe.throw(f"POS Payment Session {session.name} is {session.status}.")


def assert_amount_available(session, amount):
    amount = flt(amount, 3)
    available = flt(available_to_allocate(session), 3)
    if amount <= 0:
        frappe.throw("Payment amount must be greater than zero.")
    if amount > available:
        frappe.throw(
            f"Maximum allocatable amount is {available:.3f} {session.currency}."
        )
    return amount


def next_sequence(session_name):
    value = frappe.db.sql(
        """select coalesce(max(sequence), 0) from `tabPOS Payment Allocation` where session=%s""",
        session_name,
    )[0][0]
    return int(value or 0) + 1


def get_existing_by_idempotency(doctype, key):
    if not key:
        return None
    name = frappe.db.get_value(doctype, {"idempotency_key": key}, "name")
    return frappe.get_doc(doctype, name) if name else None


def make_idempotency(prefix, session_name):
    return f"{prefix}:{session_name}:{uuid.uuid4().hex[:16]}"


def customer_payload(session, mobile_number=None):
    name = session.customer_name or "Customer"
    email = session.email or ""
    raw_mobile = mobile_number or session.mobile_number or ""

    if session.customer and frappe.db.exists("Customer", session.customer):
        customer = frappe.get_doc("Customer", session.customer)
        name = session.customer_name or getattr(customer, "customer_name", None) or session.customer
        email = email or getattr(customer, "email_id", None) or ""
        raw_mobile = raw_mobile or getattr(customer, "mobile_no", None) or ""

    country_code, phone = _normalize_kuwait_phone(raw_mobile)
    return {
        "name": name,
        "email": email,
        "phone": phone,
        "phone_country_code": country_code,
    }


def provider_webhook_url(account):
    method = {
        "Tap Payments": "tap",
        "MyFatoorah": "myfatoorah",
        "UPayments": "upayments",
    }[account.provider]
    return (
        f"{frappe.utils.get_url()}/api/method/"
        f"erpnext_payment_hub.webhook.{method}?provider_account={account.name}"
    )


def default_return_url():
    return (
        f"{frappe.utils.get_url()}/api/method/"
        "erpnext_payment_hub.api.payment_return"
    )


def resolve_online_account(explicit=None):
    if explicit:
        return get_provider_account(explicit, for_new_transaction=True)
    settings = get_settings()
    if getattr(settings, "online_provider", None):
        return get_provider_account(settings.online_provider, for_new_transaction=True)
    return get_provider_account(None, for_new_transaction=True)


def resolve_physical_account(session, explicit=None):
    if explicit:
        return get_provider_account(explicit, for_new_transaction=True)
    if session.pos_station:
        station = frappe.get_doc("POS Station", session.pos_station)
        if station.provider_account:
            return get_provider_account(station.provider_account, for_new_transaction=True)
    settings = get_settings()
    if getattr(settings, "physical_terminal_provider", None):
        return get_provider_account(settings.physical_terminal_provider, for_new_transaction=True)
    frappe.throw("Set Physical Terminal Provider in Payment Hub Settings or POS Station.")


def create_allocation(
    session,
    *,
    channel,
    amount,
    mode_of_payment=None,
    status="Draft",
    idempotency_key=None,
    mobile_number=None,
    provider_account=None,
    provider=None,
    payment_terminal=None,
    terminal_id=None,
):
    if idempotency_key:
        existing = get_existing_by_idempotency("POS Payment Allocation", idempotency_key)
        if existing:
            return existing

    doc = frappe.new_doc("POS Payment Allocation")
    doc.session = session.name
    doc.sequence = next_sequence(session.name)
    doc.channel = channel
    doc.status = status
    doc.mode_of_payment = mode_of_payment
    doc.amount = flt(amount, 3)
    doc.currency = session.currency
    doc.idempotency_key = idempotency_key or make_idempotency(channel[:3].upper(), session.name)
    doc.mobile_number = mobile_number
    doc.provider_account = provider_account
    doc.provider = provider
    doc.payment_terminal = payment_terminal
    doc.terminal_id = terminal_id
    doc.insert(ignore_permissions=True)
    return doc


def sync_allocation_from_gateway(gateway_doc):
    allocation_name = getattr(gateway_doc, "pos_payment_allocation", None)
    if not allocation_name or not frappe.db.exists("POS Payment Allocation", allocation_name):
        return None

    allocation = frappe.get_doc("POS Payment Allocation", allocation_name)
    old_status = allocation.status
    allocation.provider_account = gateway_doc.provider_account
    allocation.provider = gateway_doc.provider
    allocation.actual_payment_method = gateway_doc.provider_payment_type or allocation.actual_payment_method
    allocation.payment_url = gateway_doc.payment_url or allocation.payment_url

    if gateway_doc.status == "Captured":
        allocation.status = "Captured"
        if not allocation.captured_at:
            allocation.captured_at = now_datetime()
    elif gateway_doc.status == "Failed":
        allocation.status = "Failed"
    elif gateway_doc.status == "Pending":
        allocation.status = "Waiting"

    allocation.save(ignore_permissions=True)
    session = recalculate_session(allocation.session, publish=True)
    if old_status != allocation.status:
        _publish_session(session)
    return allocation


def reconcile_pending_pos_payments(limit=20):
    names = frappe.get_all(
        "POS Payment Allocation",
        filters={"status": "Waiting", "gateway_transaction": ["is", "set"]},
        pluck="name",
        order_by="modified asc",
        limit=int(limit or 20),
    )
    for name in names:
        try:
            allocation = frappe.get_doc("POS Payment Allocation", name)
            txn = frappe.get_doc("Gateway Transaction", allocation.gateway_transaction)
            account = get_provider_account(txn.provider_account)
            provider = get_provider(account)
            normalized = provider.get_payment_status(txn)
            update_transaction_from_status(txn, normalized)
        except Exception:
            frappe.log_error(
                title=f"Payment Hub POS reconciliation failed: {name}",
                message=frappe.get_traceback(),
            )



def expire_stale_pos_payments(limit=100):
    """Expire stale local Waiting allocations after a final provider status check.

    This is a local queue expiry, not a guarantee that the remote payment link was
    revoked. A later provider webhook can still move an allocation to Captured.
    """
    settings = get_settings()
    if not bool(getattr(settings, "auto_expire_pending_sales", 1)):
        return
    minutes = int(getattr(settings, "payment_link_expiry_minutes", 30) or 30)
    cutoff = add_to_date(now_datetime(), minutes=-minutes)
    names = frappe.get_all(
        "POS Payment Allocation",
        filters={
            "status": "Waiting",
            "channel": "Electronic Payment",
            "creation": ["<", cutoff],
        },
        pluck="name",
        order_by="creation asc",
        limit=int(limit or 100),
    )
    for name in names:
        try:
            allocation = frappe.get_doc("POS Payment Allocation", name)
            if allocation.gateway_transaction:
                txn = frappe.get_doc("Gateway Transaction", allocation.gateway_transaction)
                account = get_provider_account(txn.provider_account)
                provider = get_provider(account)
                normalized = provider.get_payment_status(txn)
                update_transaction_from_status(txn, normalized)
                allocation.reload()
            if allocation.status == "Waiting":
                allocation.status = "Expired"
                allocation.save(ignore_permissions=True)
                recalculate_session(allocation.session, publish=True)
        except Exception:
            frappe.log_error(
                title=f"Payment Hub POS expiry failed: {name}",
                message=frappe.get_traceback(),
            )

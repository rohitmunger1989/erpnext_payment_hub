from __future__ import annotations

import frappe
from frappe.utils import flt, now_datetime

from erpnext_payment_hub.gateway import (
    create_gateway_transaction,
    get_provider,
    resolve_payment_terminal,
    resolve_pos_station,
    touch_pos_station,
    update_transaction_from_status,
)
from erpnext_payment_hub.pos.service import (
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
    doc.insert(ignore_permissions=True)
    recalculate_session(doc, publish=False)
    return _session_response(doc)


@frappe.whitelist()
def save_pos_draft(session_name, draft_payload, cart_reference=None):
    session = frappe.get_doc("POS Payment Session", session_name)
    assert_open_session(session)
    session.draft_payload = as_json(draft_payload)
    if cart_reference is not None:
        session.cart_reference = cart_reference
    session.save(ignore_permissions=True)
    return _session_response(session)


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
        if allocation.status == "Captured":
            allocation.captured_at = now_datetime()
        allocation.save(ignore_permissions=True)
        recalculate_session(session)
    except Exception:
        allocation.status = "Failed"
        allocation.failed_reason = frappe.get_traceback()[-2000:]
        allocation.save(ignore_permissions=True)
        recalculate_session(session)
        raise

    return {
        "allocation": _allocation_response(allocation),
        "session": _session_response(session),
        "payment_message": compose_payment_message(allocation.name),
    }


@frappe.whitelist()
def compose_payment_message(allocation_name):
    allocation = frappe.get_doc("POS Payment Allocation", allocation_name)
    session = frappe.get_doc("POS Payment Session", allocation.session)
    if not allocation.payment_url:
        frappe.throw("Payment URL is not available for this allocation.")
    return (
        f"Payment request {session.name}\n"
        f"Amount: {session.currency} {flt(allocation.amount, 3):.3f}\n"
        "Please complete your payment using the secure link below:\n"
        f"{allocation.payment_url}"
    )


@frappe.whitelist()
def mark_payment_link_sent(allocation_name, sent_via=None):
    allocation = frappe.get_doc("POS Payment Allocation", allocation_name)
    allocation.link_sent_at = now_datetime()
    allocation.link_sent_via = sent_via or (getattr(get_settings(), "whatsapp_integration", None) or "Frappe WhatsApp")
    allocation.save(ignore_permissions=True)
    return _allocation_response(allocation)


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
def finalize_pos_session(session_name, invoice_doctype, invoice_name):
    session = recalculate_session(session_name)
    if flt(session.confirmed_paid_amount, 3) < flt(session.grand_total, 3):
        frappe.throw(
            f"Session is not fully paid. Remaining confirmed amount: "
            f"{flt(session.grand_total,3)-flt(session.confirmed_paid_amount,3):.3f} {session.currency}."
        )
    if not frappe.db.exists(invoice_doctype, invoice_name):
        frappe.throw(f"{invoice_doctype} {invoice_name} does not exist.")

    session.invoice_doctype = invoice_doctype
    session.invoice_name = invoice_name
    session.finalized = 1
    session.status = "Completed"
    session.completed_at = now_datetime()
    session.save(ignore_permissions=True)

    # Re-link provider transactions from the temporary POS session to the final invoice.
    txns = frappe.get_all(
        "Gateway Transaction",
        filters={"pos_payment_session": session.name, "transaction_type": "Payment"},
        pluck="name",
    )
    for name in txns:
        txn = frappe.get_doc("Gateway Transaction", name)
        txn.reference_doctype = invoice_doctype
        txn.reference_name = invoice_name
        txn.save(ignore_permissions=True)

    return _session_response(session)


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

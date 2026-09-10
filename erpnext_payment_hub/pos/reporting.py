from __future__ import annotations

from collections import defaultdict

import frappe
from frappe.utils import add_days, flt, getdate, nowdate

from erpnext_payment_hub.pos.scope import effective_pos_profile, validate_shift_scope


ACTIVE_REFUND_STATUSES = ("Reserved", "Processing", "Pending", "Completed", "Manual Review")


def _require_transaction_read():
    if frappe.session.user == "Guest":
        frappe.throw("Login is required.", frappe.PermissionError)
    if not frappe.has_permission("Sales Invoice", "read"):
        frappe.throw("You do not have permission to view POS transactions.", frappe.PermissionError)


def _settings():
    return frappe.get_single("Payment Hub Settings")


def _norm(value):
    return " ".join(str(value or "").strip().lower().split())


def _channel_from_mode(mode_of_payment, settings=None):
    settings = settings or _settings()
    value = _norm(mode_of_payment)
    if value == _norm(getattr(settings, "cash_mode_of_payment", None) or "Cash"):
        return "Cash"
    if value == _norm(
        getattr(settings, "physical_mode_of_payment", None) or "Physical Payment Terminal"
    ):
        return "Physical Payment Terminal"
    if value == _norm(
        getattr(settings, "electronic_mode_of_payment", None) or "Electronic Payment"
    ):
        return "Electronic Payment"
    return "Other"


def _gateway_search_invoice_names(search):
    if not search:
        return set()
    term = f"%{search}%"
    gateway_rows = frappe.get_all(
        "Gateway Transaction",
        or_filters={
            "name": ["like", term],
            "provider_transaction_id": ["like", term],
            "provider_order_id": ["like", term],
            "provider_payment_id": ["like", term],
            "provider_tracking_id": ["like", term],
            "provider_refund_id": ["like", term],
            "reference_name": ["like", term],
        },
        fields=["name", "reference_doctype", "reference_name", "pos_payment_session"],
        limit=200,
    )
    names = {
        row.reference_name
        for row in gateway_rows
        if row.reference_doctype == "Sales Invoice" and row.reference_name
    }
    session_names = {row.pos_payment_session for row in gateway_rows if row.pos_payment_session}
    if session_names:
        names.update(
            frappe.get_all(
                "POS Payment Session",
                filters={"name": ["in", list(session_names)]},
                pluck="invoice_name",
            )
        )
    gateway_names = [row.name for row in gateway_rows]
    if gateway_names:
        refund_rows = frappe.get_all(
            "POS Refund Allocation",
            filters={"refund_gateway_transaction": ["in", gateway_names]},
            fields=["original_invoice", "return_invoice"],
        )
        for row in refund_rows:
            if row.original_invoice:
                names.add(row.original_invoice)
            if row.return_invoice:
                names.add(row.return_invoice)
    pra_rows = frappe.get_all(
        "POS Refund Allocation",
        or_filters={
            "name": ["like", term],
            "original_invoice": ["like", term],
            "return_invoice": ["like", term],
            "source_allocation": ["like", term],
            "refund_gateway_transaction": ["like", term],
        },
        fields=["original_invoice", "return_invoice"],
        limit=200,
    )
    for row in pra_rows:
        if row.original_invoice:
            names.add(row.original_invoice)
        if row.return_invoice:
            names.add(row.return_invoice)
    return {name for name in names if name}


def _invoice_payment_rows(
    *,
    from_date=None,
    to_date=None,
    pos_profile=None,
    pos_opening_shift=None,
    cashier=None,
    search=None,
    limit=1000,
):
    conditions = ["si.docstatus = 1", "IFNULL(si.is_pos, 0) = 1"]
    params = {}
    if from_date:
        conditions.append("si.posting_date >= %(from_date)s")
        params["from_date"] = str(getdate(from_date))
    if to_date:
        conditions.append("si.posting_date <= %(to_date)s")
        params["to_date"] = str(getdate(to_date))
    if pos_profile:
        conditions.append("si.pos_profile = %(pos_profile)s")
        params["pos_profile"] = pos_profile
    has_shift_field = frappe.db.has_column("Sales Invoice", "posa_pos_opening_shift")
    if pos_opening_shift and has_shift_field:
        conditions.append("si.posa_pos_opening_shift = %(pos_opening_shift)s")
        params["pos_opening_shift"] = pos_opening_shift
    if cashier:
        conditions.append("si.owner = %(cashier)s")
        params["cashier"] = cashier

    extra_names = _gateway_search_invoice_names(search)
    if search:
        params["term"] = f"%{search}%"
        search_parts = [
            "si.name LIKE %(term)s",
            "IFNULL(si.return_against, '') LIKE %(term)s",
            "IFNULL(si.customer, '') LIKE %(term)s",
            "IFNULL(si.customer_name, '') LIKE %(term)s",
            "IFNULL(si.contact_mobile, '') LIKE %(term)s",
            "IFNULL(sip.mode_of_payment, '') LIKE %(term)s",
        ]
        if extra_names:
            placeholders = []
            for idx, name in enumerate(sorted(extra_names)):
                key = f"extra_{idx}"
                params[key] = name
                placeholders.append(f"%({key})s")
            search_parts.append(f"si.name IN ({','.join(placeholders)})")
        conditions.append("(" + " OR ".join(search_parts) + ")")

    safe_limit = max(1, min(int(limit or 1000), 5000))
    sql = f"""
        SELECT
            si.name AS invoice,
            si.is_return,
            si.return_against,
            si.posting_date,
            si.posting_time,
            si.creation,
            si.owner AS cashier_user,
            si.customer,
            si.customer_name,
            si.contact_mobile AS mobile_number,
            si.pos_profile,
            {"si.posa_pos_opening_shift" if has_shift_field else "NULL"} AS pos_opening_shift,
            si.currency,
            si.grand_total,
            sip.mode_of_payment,
            sip.amount AS payment_amount,
            sip.account AS payment_account
        FROM `tabSales Invoice` si
        INNER JOIN `tabSales Invoice Payment` sip
            ON sip.parent = si.name
            AND sip.parenttype = 'Sales Invoice'
        WHERE {' AND '.join(conditions)}
        ORDER BY si.posting_date DESC, si.posting_time DESC, si.creation DESC
        LIMIT {safe_limit}
    """
    return frappe.db.sql(sql, params, as_dict=True)


def _prefetch_payment_hub(invoice_names):
    invoice_names = [name for name in invoice_names if name]
    sessions = {}
    sessions_by_invoice = {}
    allocations_by_session = defaultdict(list)
    refunds_by_return = defaultdict(list)
    gateways = {}
    auths = {}
    if not invoice_names:
        return sessions, sessions_by_invoice, allocations_by_session, refunds_by_return, gateways, auths

    session_rows = frappe.get_all(
        "POS Payment Session",
        filters={"invoice_name": ["in", invoice_names]},
        fields=[
            "name", "invoice_name", "customer", "customer_name", "mobile_number", "currency",
            "pos_profile", "pos_station", "pos_opening_shift", "business_date",
            "owner", "status", "grand_total", "creation",
        ],
    )
    for row in session_rows:
        sessions[row.name] = row
        sessions_by_invoice[row.invoice_name] = row

    if sessions:
        allocs = frappe.get_all(
            "POS Payment Allocation",
            filters={"session": ["in", list(sessions)]},
            fields=[
                "name", "session", "sequence", "channel", "status", "mode_of_payment", "amount",
                "currency", "gateway_transaction", "provider_account", "provider",
                "actual_payment_method", "payment_terminal", "terminal_id", "captured_at", "creation",
            ],
            order_by="session asc, sequence asc, creation asc",
        )
        for row in allocs:
            allocations_by_session[row.session].append(row)

    refunds = frappe.get_all(
        "POS Refund Allocation",
        filters={"return_invoice": ["in", invoice_names]},
        fields=[
            "name", "original_invoice", "return_invoice", "source_allocation",
            "source_gateway_transaction", "refund_gateway_transaction", "channel", "mode_of_payment",
            "actual_refund_channel", "actual_refund_mode_of_payment", "amount", "currency",
            "provider_account", "provider", "actual_payment_method", "status", "provider_status",
            "is_override", "authorization", "authorized_by", "initiated_by", "completed_at",
            "override_reason", "creation",
        ],
        order_by="creation asc",
    )
    for row in refunds:
        refunds_by_return[row.return_invoice].append(row)

    gateway_names = set()
    for rows in allocations_by_session.values():
        gateway_names.update(row.gateway_transaction for row in rows if row.gateway_transaction)
    for rows in refunds_by_return.values():
        gateway_names.update(row.source_gateway_transaction for row in rows if row.source_gateway_transaction)
        gateway_names.update(row.refund_gateway_transaction for row in rows if row.refund_gateway_transaction)
    if gateway_names:
        for row in frappe.get_all(
            "Gateway Transaction",
            filters={"name": ["in", list(gateway_names)]},
            fields=[
                "name", "transaction_type", "status", "provider", "provider_account", "payment_method",
                "amount", "currency", "refunded_amount", "provider_transaction_id", "provider_order_id",
                "provider_payment_id", "provider_tracking_id", "provider_refund_id", "provider_payment_type",
                "original_transaction", "creation", "modified",
            ],
        ):
            gateways[row.name] = row

    auth_names = {
        row.authorization
        for rows in refunds_by_return.values()
        for row in rows
        if row.authorization
    }
    if auth_names:
        for row in frappe.get_all(
            "Payment Hub Refund Authorization",
            filters={"name": ["in", list(auth_names)]},
            fields=["name", "cashier_user", "authorized_by", "action", "reason", "is_override", "authorized_at"],
        ):
            auths[row.name] = row

    return sessions, sessions_by_invoice, allocations_by_session, refunds_by_return, gateways, auths


def _refund_reserved_map(source_names):
    result = defaultdict(float)
    if not source_names:
        return result
    rows = frappe.get_all(
        "POS Refund Allocation",
        filters={
            "source_allocation": ["in", list(source_names)],
            "status": ["in", list(ACTIVE_REFUND_STATUSES)],
        },
        fields=["source_allocation", "amount"],
    )
    for row in rows:
        result[row.source_allocation] += flt(row.amount, 3)
    return result


def _provider_refs(gateway):
    if not gateway:
        return {
            "gateway_transaction": None,
            "provider_transaction_id": None,
            "provider_payment_id": None,
            "provider_tracking_id": None,
            "provider_refund_id": None,
        }
    return {
        "gateway_transaction": gateway.name,
        "provider_transaction_id": gateway.provider_transaction_id,
        "provider_payment_id": gateway.provider_payment_id,
        "provider_tracking_id": gateway.provider_tracking_id,
        "provider_refund_id": gateway.provider_refund_id,
    }


def _make_rows(invoice_payment_rows):
    settings = _settings()
    grouped = defaultdict(list)
    invoice_meta = {}
    for row in invoice_payment_rows:
        grouped[row.invoice].append(row)
        invoice_meta[row.invoice] = row

    (
        sessions,
        sessions_by_invoice,
        allocations_by_session,
        refunds_by_return,
        gateways,
        auths,
    ) = _prefetch_payment_hub(list(grouped))

    all_source_names = {
        alloc.name for rows in allocations_by_session.values() for alloc in rows
    }
    refund_reserved = _refund_reserved_map(all_source_names)
    result = []

    for invoice, payment_rows in grouped.items():
        meta = invoice_meta[invoice]
        if meta.is_return and refunds_by_return.get(invoice):
            for refund in refunds_by_return[invoice]:
                gateway = gateways.get(refund.refund_gateway_transaction)
                source_gateway = gateways.get(refund.source_gateway_transaction)
                auth = auths.get(refund.authorization)
                actual_channel = refund.actual_refund_channel or refund.channel
                actual_mode = refund.actual_refund_mode_of_payment or refund.mode_of_payment
                refs = _provider_refs(gateway)
                result.append({
                    "transaction_type": "Refund",
                    "transaction_datetime": refund.completed_at or refund.creation or meta.creation,
                    "posting_date": meta.posting_date,
                    "invoice": refund.original_invoice,
                    "return_invoice": refund.return_invoice,
                    "customer": meta.customer,
                    "customer_name": meta.customer_name,
                    "mobile_number": meta.mobile_number,
                    "pos_profile": meta.pos_profile,
                    "pos_station": None,
                    "pos_opening_shift": meta.pos_opening_shift,
                    "business_date": meta.posting_date,
                    "cashier_user": refund.initiated_by or meta.cashier_user,
                    "authorized_by": refund.authorized_by or (auth.authorized_by if auth else None),
                    "channel": actual_channel,
                    "mode_of_payment": actual_mode,
                    "original_channel": refund.channel,
                    "original_mode_of_payment": refund.mode_of_payment,
                    "provider": None if refund.is_override and actual_channel == "Cash" else refund.provider,
                    "original_provider": refund.provider,
                    "actual_payment_method": refund.actual_payment_method,
                    "amount": flt(refund.amount, 3),
                    "signed_amount": -flt(refund.amount, 3),
                    "currency": refund.currency or meta.currency,
                    "status": refund.provider_status or refund.status,
                    "payment_hub_managed": True,
                    "is_override": bool(refund.is_override),
                    "override_reason": refund.override_reason,
                    "authorization": refund.authorization,
                    "refund_allocation": refund.name,
                    "source_allocation": refund.source_allocation,
                    "source_gateway_transaction": refund.source_gateway_transaction,
                    "original_provider_transaction_id": source_gateway.provider_transaction_id if source_gateway else None,
                    "remaining_refundable": None,
                    **refs,
                })
            continue

        session = sessions_by_invoice.get(invoice)
        if not meta.is_return and session:
            for allocation in allocations_by_session.get(session.name, []):
                if allocation.status not in ("Captured", "Refunded"):
                    continue
                gateway = gateways.get(allocation.gateway_transaction)
                reserved = flt(refund_reserved.get(allocation.name), 3)
                gateway_refunded = flt(gateway.refunded_amount if gateway else 0, 3)
                refundable = max(flt(allocation.amount, 3) - max(reserved, gateway_refunded), 0)
                refs = _provider_refs(gateway)
                result.append({
                    "transaction_type": "Payment",
                    "transaction_datetime": allocation.captured_at or allocation.creation or meta.creation,
                    "posting_date": meta.posting_date,
                    "invoice": invoice,
                    "return_invoice": None,
                    "customer": session.customer or meta.customer,
                    "customer_name": session.customer_name or meta.customer_name,
                    "mobile_number": session.mobile_number or meta.mobile_number,
                    "pos_profile": session.pos_profile or meta.pos_profile,
                    "pos_station": session.pos_station,
                    "pos_opening_shift": session.pos_opening_shift or meta.pos_opening_shift,
                    "business_date": session.business_date or meta.posting_date,
                    "cashier_user": session.owner or meta.cashier_user,
                    "authorized_by": None,
                    "channel": allocation.channel,
                    "mode_of_payment": allocation.mode_of_payment,
                    "original_channel": allocation.channel,
                    "original_mode_of_payment": allocation.mode_of_payment,
                    "provider": allocation.provider,
                    "original_provider": allocation.provider,
                    "actual_payment_method": allocation.actual_payment_method,
                    "amount": flt(allocation.amount, 3),
                    "signed_amount": flt(allocation.amount, 3),
                    "currency": allocation.currency or meta.currency,
                    "status": gateway.status if gateway else allocation.status,
                    "payment_hub_managed": True,
                    "is_override": False,
                    "override_reason": None,
                    "authorization": None,
                    "refund_allocation": None,
                    "source_allocation": allocation.name,
                    "source_gateway_transaction": allocation.gateway_transaction,
                    "original_provider_transaction_id": gateway.provider_transaction_id if gateway else None,
                    "remaining_refundable": flt(refundable, 3),
                    **refs,
                })
            continue

        # Non-Payment-Hub POS invoice/return: ERPNext payment row is the audit source.
        for payment in payment_rows:
            amount = abs(flt(payment.payment_amount, 3))
            transaction_type = "Refund" if meta.is_return else "Payment"
            channel = _channel_from_mode(payment.mode_of_payment, settings)
            result.append({
                "transaction_type": transaction_type,
                "transaction_datetime": meta.creation,
                "posting_date": meta.posting_date,
                "invoice": meta.return_against if meta.is_return else invoice,
                "return_invoice": invoice if meta.is_return else None,
                "customer": meta.customer,
                "customer_name": meta.customer_name,
                "mobile_number": meta.mobile_number,
                "pos_profile": meta.pos_profile,
                "pos_station": None,
                "pos_opening_shift": meta.pos_opening_shift,
                "business_date": meta.posting_date,
                "cashier_user": meta.cashier_user,
                "authorized_by": None,
                "channel": channel,
                "mode_of_payment": payment.mode_of_payment,
                "original_channel": channel,
                "original_mode_of_payment": payment.mode_of_payment,
                "provider": None,
                "original_provider": None,
                "actual_payment_method": None,
                "amount": amount,
                "signed_amount": -amount if meta.is_return else amount,
                "currency": meta.currency,
                "status": "Refund Recorded" if meta.is_return else "Paid",
                "payment_hub_managed": False,
                "is_override": False,
                "override_reason": None,
                "authorization": None,
                "refund_allocation": None,
                "source_allocation": None,
                "source_gateway_transaction": None,
                "original_provider_transaction_id": None,
                "remaining_refundable": None,
                "gateway_transaction": None,
                "provider_transaction_id": None,
                "provider_payment_id": None,
                "provider_tracking_id": None,
                "provider_refund_id": None,
            })

    result.sort(key=lambda row: str(row.get("transaction_datetime") or ""), reverse=True)
    return result


def _pending_session_rows(
    *, pos_profile=None, pos_opening_shift=None, from_date=None, to_date=None, search=None, limit=100
):
    # Draft invoices created by preflight are still pending transactions until
    # submitted, so do not require invoice_name to be empty here.
    filters = {"status": ["not in", ["Completed", "Cancelled"]]}
    if pos_profile:
        filters["pos_profile"] = pos_profile
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
        term = f"%{search}%"
        or_filters = {
            "name": ["like", term],
            "cart_reference": ["like", term],
            "customer": ["like", term],
            "customer_name": ["like", term],
            "mobile_number": ["like", term],
        }
    sessions = frappe.get_all(
        "POS Payment Session",
        filters=filters,
        or_filters=or_filters,
        fields=[
            "name", "status", "customer", "customer_name", "mobile_number", "currency",
            "grand_total", "confirmed_paid_amount", "pending_amount", "pos_profile", "pos_station",
            "pos_opening_shift", "business_date", "owner", "creation",
        ],
        order_by="creation desc",
        limit=max(1, min(int(limit or 100), 500)),
    )
    result = []
    for session in sessions:
        allocations = frappe.get_all(
            "POS Payment Allocation",
            filters={"session": session.name},
            fields=["name", "channel", "mode_of_payment", "amount", "status", "provider", "actual_payment_method", "gateway_transaction", "creation"],
            order_by="sequence asc",
        )
        if not allocations:
            allocations = [frappe._dict({
                "name": None, "channel": None, "mode_of_payment": None,
                "amount": session.grand_total, "status": session.status, "provider": None,
                "actual_payment_method": None, "gateway_transaction": None, "creation": session.creation,
            })]
        for allocation in allocations:
            gateway = None
            if allocation.gateway_transaction:
                gateway = frappe.db.get_value(
                    "Gateway Transaction",
                    allocation.gateway_transaction,
                    ["name", "status", "provider_transaction_id", "provider_payment_id", "provider_tracking_id"],
                    as_dict=True,
                )
            searchable = " ".join(
                str(value or "")
                for value in [session.name, session.customer, session.customer_name, session.mobile_number,
                              allocation.name, allocation.gateway_transaction,
                              gateway.provider_transaction_id if gateway else None,
                              gateway.provider_payment_id if gateway else None,
                              gateway.provider_tracking_id if gateway else None]
            ).lower()
            if search and search.lower() not in searchable:
                continue
            result.append({
                "transaction_type": "Pending Payment",
                "transaction_datetime": allocation.creation or session.creation,
                "posting_date": None,
                "invoice": None,
                "return_invoice": None,
                "session": session.name,
                "customer": session.customer,
                "customer_name": session.customer_name,
                "mobile_number": session.mobile_number,
                "pos_profile": session.pos_profile,
                "pos_station": session.pos_station,
                "pos_opening_shift": session.pos_opening_shift,
                "business_date": session.business_date,
                "cashier_user": session.owner,
                "authorized_by": None,
                "channel": allocation.channel,
                "mode_of_payment": allocation.mode_of_payment,
                "provider": allocation.provider,
                "actual_payment_method": allocation.actual_payment_method,
                "amount": flt(allocation.amount, 3),
                "signed_amount": 0,
                "currency": session.currency,
                "status": gateway.status if gateway else allocation.status,
                "session_status": session.status,
                "payment_hub_managed": True,
                "gateway_transaction": allocation.gateway_transaction,
                "provider_transaction_id": gateway.provider_transaction_id if gateway else None,
                "provider_payment_id": gateway.provider_payment_id if gateway else None,
                "provider_tracking_id": gateway.provider_tracking_id if gateway else None,
                "provider_refund_id": None,
                "remaining_refundable": None,
            })
    return result


def transaction_history(
    *,
    search=None,
    from_date=None,
    to_date=None,
    pos_profile=None,
    pos_opening_shift=None,
    cashier=None,
    transaction_type=None,
    channel=None,
    provider=None,
    status=None,
    include_pending=True,
    limit=100,
):
    _require_transaction_read()
    safe_limit = max(1, min(int(limit or 100), 5000))
    if not search and not from_date and not pos_opening_shift:
        from_date = add_days(nowdate(), -30)
    invoice_rows = _invoice_payment_rows(
        from_date=from_date,
        to_date=to_date,
        pos_profile=pos_profile,
        pos_opening_shift=pos_opening_shift,
        cashier=cashier,
        search=search,
        limit=max(safe_limit * 5, 200),
    )
    rows = _make_rows(invoice_rows)
    if include_pending:
        rows.extend(
            _pending_session_rows(
                pos_profile=pos_profile,
                pos_opening_shift=pos_opening_shift,
                from_date=from_date,
                to_date=to_date,
                search=search,
                limit=safe_limit,
            )
        )

    def keep(row):
        if transaction_type and row.get("transaction_type") != transaction_type:
            return False
        if channel and row.get("channel") != channel:
            return False
        if provider and row.get("provider") != provider:
            return False
        if status and _norm(row.get("status")) != _norm(status):
            return False
        if search:
            haystack = " ".join(
                str(row.get(key) or "")
                for key in (
                    "transaction_type", "invoice", "return_invoice", "session", "customer", "customer_name",
                    "mobile_number", "mode_of_payment", "provider", "actual_payment_method", "status",
                    "gateway_transaction", "source_gateway_transaction", "provider_transaction_id",
                    "provider_payment_id", "provider_tracking_id", "provider_refund_id", "refund_allocation",
                )
            ).lower()
            if search.lower() not in haystack:
                return False
        return True

    rows = [row for row in rows if keep(row)]
    rows.sort(key=lambda row: str(row.get("transaction_datetime") or ""), reverse=True)
    return rows[:safe_limit]


@frappe.whitelist()
def search_transaction_history(
    search=None,
    from_date=None,
    to_date=None,
    pos_profile=None,
    pos_opening_shift=None,
    current_pos_profile=None,
    cashier=None,
    transaction_type=None,
    channel=None,
    provider=None,
    status=None,
    limit=100,
):
    pos_profile = effective_pos_profile(pos_profile, current_pos_profile)
    validate_shift_scope(pos_opening_shift=pos_opening_shift, pos_profile=pos_profile)
    rows = transaction_history(
        search=search,
        from_date=from_date,
        to_date=to_date,
        pos_profile=pos_profile,
        pos_opening_shift=pos_opening_shift,
        cashier=cashier,
        transaction_type=transaction_type,
        channel=channel,
        provider=provider,
        status=status,
        include_pending=True,
        limit=limit,
    )
    return {"rows": rows, "count": len(rows)}


def daily_report_data(
    *,
    from_date=None,
    to_date=None,
    pos_profile=None,
    pos_opening_shift=None,
    cashier=None,
    transaction_type=None,
    channel=None,
    provider=None,
    status=None,
    search=None,
    limit=5000,
):
    _require_transaction_read()
    if not pos_opening_shift:
        from_date = from_date or nowdate()
        to_date = to_date or from_date
    rows = transaction_history(
        search=search,
        from_date=from_date,
        to_date=to_date,
        pos_profile=pos_profile,
        pos_opening_shift=pos_opening_shift,
        cashier=cashier,
        transaction_type=transaction_type,
        channel=channel,
        provider=provider,
        status=status,
        include_pending=False,
        limit=limit,
    )

    payment_total = flt(sum(row["amount"] for row in rows if row["transaction_type"] == "Payment"), 3)
    refund_total = flt(sum(row["amount"] for row in rows if row["transaction_type"] == "Refund"), 3)
    channel_summary = defaultdict(lambda: {"payments": 0.0, "refunds": 0.0})
    provider_summary = defaultdict(lambda: {"payments": 0.0, "refunds": 0.0})
    for row in rows:
        bucket = "refunds" if row["transaction_type"] == "Refund" else "payments"
        channel_summary[row.get("channel") or "Other"][bucket] += flt(row["amount"], 3)
        provider_summary[row.get("provider") or "Non-Gateway"][bucket] += flt(row["amount"], 3)

    def summary_rows(mapping):
        result = []
        for key, totals in sorted(mapping.items()):
            payments = flt(totals["payments"], 3)
            refunds = flt(totals["refunds"], 3)
            result.append({"name": key, "payments": payments, "refunds": refunds, "net": flt(payments - refunds, 3)})
        return result

    row_dates = [str(row.get("posting_date") or row.get("business_date")) for row in rows if row.get("posting_date") or row.get("business_date")]
    display_from = str(getdate(from_date)) if from_date else (min(row_dates) if row_dates else nowdate())
    display_to = str(getdate(to_date)) if to_date else (max(row_dates) if row_dates else display_from)

    return {
        "from_date": display_from,
        "to_date": display_to,
        "currency": rows[0]["currency"] if rows else "KWD",
        "payment_total": payment_total,
        "refund_total": refund_total,
        "net_total": flt(payment_total - refund_total, 3),
        "transaction_count": len(rows),
        "channel_summary": summary_rows(channel_summary),
        "provider_summary": summary_rows(provider_summary),
        "rows": rows,
    }


@frappe.whitelist()
def get_daily_transaction_report(
    from_date=None,
    to_date=None,
    pos_profile=None,
    pos_opening_shift=None,
    current_pos_profile=None,
    cashier=None,
    transaction_type=None,
    channel=None,
    provider=None,
    status=None,
    search=None,
):
    pos_profile = effective_pos_profile(pos_profile, current_pos_profile)
    validate_shift_scope(pos_opening_shift=pos_opening_shift, pos_profile=pos_profile)
    return daily_report_data(
        from_date=from_date,
        to_date=to_date,
        pos_profile=pos_profile,
        pos_opening_shift=pos_opening_shift,
        cashier=cashier,
        transaction_type=transaction_type,
        channel=channel,
        provider=provider,
        status=status,
        search=search,
    )


def _export_value(value):
    if value is None:
        return ""
    return str(value)


def _export_reference(row):
    return (
        row.get("provider_refund_id")
        or row.get("provider_transaction_id")
        or row.get("provider_payment_id")
        or row.get("provider_tracking_id")
        or ""
    )


def _export_invoice(row):
    return row.get("return_invoice") or row.get("invoice") or row.get("session") or ""


def _export_filters_text(
    *, view, search=None, from_date=None, to_date=None, pos_profile=None, pos_opening_shift=None
):
    parts = []
    if view == "daily":
        parts.append(f"Period: {from_date or nowdate()} to {to_date or from_date or nowdate()}")
    elif search:
        parts.append(f"Search: {search}")
    else:
        parts.append("Period: last 30 days")
    if pos_profile:
        parts.append(f"POS Profile: {pos_profile}")
    if pos_opening_shift:
        parts.append(f"POS Shift: {pos_opening_shift}")
    return " | ".join(parts)


def _export_detail_headers():
    return [
        "Date / Time",
        "Type",
        "Invoice / Session",
        "Original Invoice",
        "Customer",
        "Mobile",
        "POS Profile",
        "Cashier",
        "Channel",
        "Mode of Payment",
        "Provider",
        "Method",
        "Amount",
        "Currency",
        "Status",
        "Gateway Transaction",
        "Provider Transaction / Refund ID",
        "Authorized By",
        "Override",
        "Override Reason",
        "Remaining Refundable",
    ]


def _export_detail_row(row):
    return [
        _export_value(row.get("transaction_datetime")),
        _export_value(row.get("transaction_type")),
        _export_invoice(row),
        _export_value(row.get("invoice") if row.get("return_invoice") else ""),
        _export_value(row.get("customer_name") or row.get("customer")),
        _export_value(row.get("mobile_number")),
        _export_value(row.get("pos_profile")),
        _export_value(row.get("cashier_user")),
        _export_value(row.get("channel")),
        _export_value(row.get("mode_of_payment")),
        _export_value(row.get("provider")),
        _export_value(row.get("actual_payment_method")),
        (-abs(flt(row.get("amount"), 3)) if row.get("transaction_type") == "Refund" else abs(flt(row.get("amount"), 3))),
        _export_value(row.get("currency") or "KWD"),
        _export_value(row.get("status")),
        _export_value(row.get("gateway_transaction") or row.get("source_gateway_transaction")),
        _export_reference(row),
        _export_value(row.get("authorized_by")),
        "Yes" if row.get("is_override") else "No",
        _export_value(row.get("override_reason")),
        "" if row.get("remaining_refundable") is None else flt(row.get("remaining_refundable"), 3),
    ]


def _build_export_xlsx(
    *, view, rows, report=None, search=None, from_date=None, to_date=None, pos_profile=None, pos_opening_shift=None
):
    from frappe.utils.xlsxutils import make_xlsx

    title = "Payment Hub Daily Payment & Refund Report" if view == "daily" else "Payment Hub Transaction History"
    data = [
        [title],
        [_export_filters_text(
            view=view, search=search, from_date=from_date, to_date=to_date,
            pos_profile=pos_profile, pos_opening_shift=pos_opening_shift
        )],
        [],
    ]

    if view == "daily" and report:
        currency = report.get("currency") or "KWD"
        data.extend([
            ["Summary", "Amount", "Currency"],
            ["Payments", flt(report.get("payment_total"), 3), currency],
            ["Refunds", flt(report.get("refund_total"), 3), currency],
            ["Net Collection", flt(report.get("net_total"), 3), currency],
            [],
            ["By Payment Channel", "Payments", "Refunds", "Net"],
        ])
        for item in report.get("channel_summary") or []:
            data.append([
                item.get("name"),
                flt(item.get("payments"), 3),
                flt(item.get("refunds"), 3),
                flt(item.get("net"), 3),
            ])
        data.extend([[], ["By Provider", "Payments", "Refunds", "Net"]])
        for item in report.get("provider_summary") or []:
            data.append([
                item.get("name"),
                flt(item.get("payments"), 3),
                flt(item.get("refunds"), 3),
                flt(item.get("net"), 3),
            ])
        data.append([])

    data.append(_export_detail_headers())
    data.extend(_export_detail_row(row) for row in rows)
    return make_xlsx(data, "Payment Hub").getvalue()


def _html(value):
    from html import escape

    return escape(_export_value(value))


def _amount_html(row):
    amount = abs(flt(row.get("amount"), 3))
    if row.get("transaction_type") == "Refund":
        amount = -amount
    return f"{flt(amount, 3):,.3f} {_html(row.get('currency') or 'KWD')}"


def _build_export_pdf_html(
    *, view, rows, report=None, search=None, from_date=None, to_date=None, pos_profile=None, pos_opening_shift=None
):
    title = "Payment Hub Daily Payment & Refund Report" if view == "daily" else "Payment Hub Transaction History"
    filter_text = _export_filters_text(
        view=view, search=search, from_date=from_date, to_date=to_date,
        pos_profile=pos_profile, pos_opening_shift=pos_opening_shift
    )

    summary_html = ""
    if view == "daily" and report:
        currency = _html(report.get("currency") or "KWD")
        summary_html = f"""
        <table class=\"summary\">
          <tr><th>Payments</th><th>Refunds</th><th>Net Collection</th><th>Transactions</th></tr>
          <tr>
            <td>{flt(report.get('payment_total'), 3):,.3f} {currency}</td>
            <td>{flt(report.get('refund_total'), 3):,.3f} {currency}</td>
            <td>{flt(report.get('net_total'), 3):,.3f} {currency}</td>
            <td>{int(report.get('transaction_count') or len(rows))}</td>
          </tr>
        </table>
        """

        channel_rows = "".join(
            f"<tr><td>{_html(item.get('name'))}</td><td>{flt(item.get('payments'), 3):,.3f}</td><td>{flt(item.get('refunds'), 3):,.3f}</td><td>{flt(item.get('net'), 3):,.3f}</td></tr>"
            for item in (report.get("channel_summary") or [])
        )
        provider_rows = "".join(
            f"<tr><td>{_html(item.get('name'))}</td><td>{flt(item.get('payments'), 3):,.3f}</td><td>{flt(item.get('refunds'), 3):,.3f}</td><td>{flt(item.get('net'), 3):,.3f}</td></tr>"
            for item in (report.get("provider_summary") or [])
        )
        summary_html += f"""
        <div class=\"grid\">
          <div><h3>By Payment Channel</h3><table><tr><th>Channel</th><th>Payments</th><th>Refunds</th><th>Net</th></tr>{channel_rows}</table></div>
          <div><h3>By Provider</h3><table><tr><th>Provider</th><th>Payments</th><th>Refunds</th><th>Net</th></tr>{provider_rows}</table></div>
        </div>
        """

    detail_rows = []
    for row in rows:
        invoice = _export_invoice(row)
        customer = row.get("customer_name") or row.get("customer") or ""
        if row.get("mobile_number"):
            customer = f"{customer}<br><span class='muted'>{_html(row.get('mobile_number'))}</span>"
        else:
            customer = _html(customer)
        provider_method = " / ".join(
            _html(value) for value in (row.get("provider"), row.get("actual_payment_method")) if value
        ) or "-"
        ref = row.get("gateway_transaction") or row.get("source_gateway_transaction") or ""
        provider_ref = _export_reference(row)
        if provider_ref:
            ref = f"{_html(ref)}<br><span class='muted'>{_html(provider_ref)}</span>"
        else:
            ref = _html(ref)
        auth = _html(row.get("authorized_by") or "-")
        if row.get("is_override"):
            auth += "<br><b>Override</b>"
            if row.get("override_reason"):
                auth += f"<br><span class='muted'>{_html(row.get('override_reason'))}</span>"
        detail_rows.append(
            "<tr>"
            f"<td>{_html(row.get('transaction_datetime'))}</td>"
            f"<td>{_html(row.get('transaction_type'))}</td>"
            f"<td>{_html(invoice)}</td>"
            f"<td>{customer}</td>"
            f"<td>{_html(row.get('pos_profile') or '-')}</td>"
            f"<td>{_html(row.get('mode_of_payment') or row.get('channel') or '-')}</td>"
            f"<td>{provider_method}</td>"
            f"<td class='num'>{_amount_html(row)}</td>"
            f"<td>{_html(row.get('status') or '-')}</td>"
            f"<td>{ref}</td>"
            f"<td>{auth}</td>"
            "</tr>"
        )

    return f"""
<!doctype html>
<html>
<head>
<meta charset=\"utf-8\">
<style>
  body {{ font-family: Arial, sans-serif; color: #222; font-size: 8.5px; }}
  h1 {{ font-size: 18px; margin: 0 0 4px; }}
  h3 {{ font-size: 10px; margin: 10px 0 4px; }}
  .meta {{ color: #666; font-size: 9px; margin-bottom: 10px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 8px; }}
  th, td {{ border: 1px solid #d9d9d9; padding: 4px; vertical-align: top; }}
  th {{ background: #f1f3f5; font-weight: 700; }}
  .summary th, .summary td {{ text-align: center; font-size: 10px; }}
  .grid {{ width: 100%; }}
  .grid > div {{ width: 49%; display: inline-block; vertical-align: top; margin-right: 1%; }}
  .num {{ text-align: right; white-space: nowrap; }}
  .muted {{ color: #777; font-size: 7.5px; }}
  .detail {{ table-layout: fixed; }}
  .detail th:nth-child(1) {{ width: 9%; }}
  .detail th:nth-child(2) {{ width: 6%; }}
  .detail th:nth-child(3) {{ width: 11%; }}
  .detail th:nth-child(4) {{ width: 11%; }}
  .detail th:nth-child(5) {{ width: 8%; }}
  .detail th:nth-child(6) {{ width: 10%; }}
  .detail th:nth-child(7) {{ width: 9%; }}
  .detail th:nth-child(8) {{ width: 8%; }}
  .detail th:nth-child(9) {{ width: 7%; }}
  .detail th:nth-child(10) {{ width: 12%; }}
  .detail th:nth-child(11) {{ width: 9%; }}
</style>
</head>
<body>
  <h1>{_html(title)}</h1>
  <div class=\"meta\">{_html(filter_text)} | Generated by {_html(frappe.session.user)}</div>
  {summary_html}
  <h3>Transactions</h3>
  <table class=\"detail\">
    <thead><tr><th>Date / Time</th><th>Type</th><th>Invoice / Session</th><th>Customer</th><th>POS</th><th>Payment</th><th>Provider / Method</th><th>Amount</th><th>Status</th><th>Gateway / Provider Ref</th><th>Authorized By</th></tr></thead>
    <tbody>{''.join(detail_rows) if detail_rows else '<tr><td colspan="11">No transactions found.</td></tr>'}</tbody>
  </table>
</body>
</html>
"""


@frappe.whitelist()
def download_transaction_export(
    view="history",
    file_format="xlsx",
    search=None,
    from_date=None,
    to_date=None,
    pos_profile=None,
    pos_opening_shift=None,
    current_pos_profile=None,
):
    """Download POS Payment Hub history/daily report as Excel or PDF."""
    _require_transaction_read()
    view = _norm(view)
    file_format = _norm(file_format)
    if view not in {"history", "daily"}:
        frappe.throw("Export view must be History or Daily.")
    if file_format not in {"xlsx", "excel", "pdf"}:
        frappe.throw("Export format must be Excel or PDF.")

    pos_profile = effective_pos_profile(pos_profile, current_pos_profile)
    validate_shift_scope(pos_opening_shift=pos_opening_shift, pos_profile=pos_profile)

    report = None
    if view == "daily":
        report = daily_report_data(
            from_date=from_date,
            to_date=to_date,
            pos_profile=pos_profile,
            pos_opening_shift=pos_opening_shift,
            limit=5000,
        )
        rows = report["rows"]
        from_date = report["from_date"]
        to_date = report["to_date"]
        base_name = f"Payment-Hub-Daily-{from_date}-to-{to_date}"
    else:
        rows = transaction_history(
            search=search,
            from_date=from_date,
            to_date=to_date,
            pos_profile=pos_profile,
            pos_opening_shift=pos_opening_shift,
            include_pending=True,
            limit=5000,
        )
        base_name = f"Payment-Hub-Transaction-History-{nowdate()}"

    if file_format in {"xlsx", "excel"}:
        frappe.local.response.filename = f"{base_name}.xlsx"
        frappe.local.response.filecontent = _build_export_xlsx(
            view=view,
            rows=rows,
            report=report,
            search=search,
            from_date=from_date,
            to_date=to_date,
            pos_profile=pos_profile,
            pos_opening_shift=pos_opening_shift,
        )
        frappe.local.response.type = "binary"
        return

    from frappe.utils.pdf import get_pdf

    html = _build_export_pdf_html(
        view=view,
        rows=rows,
        report=report,
        search=search,
        from_date=from_date,
        to_date=to_date,
        pos_profile=pos_profile,
        pos_opening_shift=pos_opening_shift,
    )
    frappe.local.response.filename = f"{base_name}.pdf"
    frappe.local.response.filecontent = get_pdf(
        html,
        {
            "orientation": "Landscape",
            "page-size": "A4",
            "margin-top": "8mm",
            "margin-bottom": "8mm",
            "margin-left": "6mm",
            "margin-right": "6mm",
        },
    )
    frappe.local.response.type = "pdf"

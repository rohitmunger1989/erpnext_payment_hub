from __future__ import annotations

import json

import frappe
from frappe.utils import cint, flt, now_datetime

from erpnext_payment_hub.pos.authorization import (
    mark_authorization_used,
    validate_refund_authorization,
)


ACTIVE_RESERVATION_STATUSES = (
    "Reserved",
    "Processing",
    "Pending",
    "Completed",
    "Manual Review",
)


def _as_list(value):
    if not value:
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, list) else []


def _get_payment_hub_session(original_invoice):
    sessions = frappe.get_all(
        "POS Payment Session",
        filters={
            "invoice_doctype": "Sales Invoice",
            "invoice_name": original_invoice,
        },
        pluck="name",
        order_by="creation desc",
        limit=1,
    )
    if sessions:
        return frappe.get_doc("POS Payment Session", sessions[0])

    # Compatibility fallback for invoices linked only through Gateway Transaction.
    txn = frappe.get_all(
        "Gateway Transaction",
        filters={
            "transaction_type": "Payment",
            "reference_doctype": "Sales Invoice",
            "reference_name": original_invoice,
            "status": "Captured",
        },
        fields=["pos_payment_session"],
        order_by="creation desc",
        limit=1,
    )
    if txn and txn[0].pos_payment_session:
        return frappe.get_doc("POS Payment Session", txn[0].pos_payment_session)
    return None


def _active_refund_amount(source_allocation, *, exclude_return_invoice=None):
    filters = {
        "source_allocation": source_allocation,
        "status": ["in", list(ACTIVE_RESERVATION_STATUSES)],
    }
    rows = frappe.get_all(
        "POS Refund Allocation",
        filters=filters,
        fields=["amount", "return_invoice"],
    )
    return flt(
        sum(
            flt(row.amount, 3)
            for row in rows
            if not exclude_return_invoice or row.return_invoice != exclude_return_invoice
        ),
        3,
    )


def _gateway_refunded_amount(gateway_transaction):
    if not gateway_transaction:
        return 0.0
    return flt(
        frappe.db.get_value(
            "Gateway Transaction", gateway_transaction, "refunded_amount"
        )
        or 0,
        3,
    )


def _source_refunded_amount(allocation, *, exclude_return_invoice=None):
    reserved = _active_refund_amount(
        allocation.name, exclude_return_invoice=exclude_return_invoice
    )
    if allocation.gateway_transaction:
        # Gateway Transaction.refunded_amount is the provider-side reserved/refunded
        # total. POS Refund Allocation also reserves in-flight return operations.
        return max(reserved, _gateway_refunded_amount(allocation.gateway_transaction))
    return reserved


def _provider_account_status(provider_account):
    if not provider_account:
        return None
    return frappe.db.get_value("Payment Provider Account", provider_account, "status")


def _authorization_required_for_channel(channel):
    settings = frappe.get_single("Payment Hub Settings")
    if channel == "Electronic Payment":
        return bool(cint(getattr(settings, "require_authorization_electronic_refund", 1)))
    if channel == "Physical Payment Terminal":
        return bool(cint(getattr(settings, "require_authorization_physical_refund", 1)))
    if channel == "Cash":
        return bool(cint(getattr(settings, "require_authorization_cash_refund", 0)))
    return False


def _cash_override_config():
    settings = frappe.get_single("Payment Hub Settings")
    enabled = bool(cint(getattr(settings, "allow_refund_method_override", 1)))
    cash_mode = getattr(settings, "cash_mode_of_payment", None) or "Cash"
    return enabled, cash_mode


def _configured_refund_channels():
    settings = frappe.get_single("Payment Hub Settings")
    return {
        getattr(settings, "cash_mode_of_payment", None) or "Cash": "Cash",
        getattr(settings, "physical_mode_of_payment", None) or "Physical Payment Terminal": "Physical Payment Terminal",
        getattr(settings, "electronic_mode_of_payment", None) or "Electronic Payment": "Electronic Payment",
    }


def _legacy_protected_payments(original_invoice):
    """Return protected payment rows for invoices that predate Payment Hub tracking.

    A Sales Invoice payment row alone proves the accounting mode, but not the provider
    transaction needed for a safe gateway refund. Electronic/terminal rows without a
    Payment Hub session are therefore treated as legacy-protected and can only be
    refunded through an explicitly authorized override (currently Cash).
    """
    doc = frappe.get_doc("Sales Invoice", original_invoice)
    channels = _configured_refund_channels()
    protected = []
    for index, payment in enumerate(doc.payments or [], start=1):
        channel = channels.get(payment.mode_of_payment)
        amount = abs(flt(payment.amount, 3))
        if channel not in ("Electronic Payment", "Physical Payment Terminal") or amount <= 0:
            continue
        protected.append({
            "legacy_source": f"LEGACY:{original_invoice}:{index}",
            "sequence": index,
            "channel": channel,
            "mode_of_payment": payment.mode_of_payment,
            "amount": amount,
        })
    return protected


def _legacy_remaining_paid_amount(original_invoice):
    original = frappe.get_doc("Sales Invoice", original_invoice)
    original_paid = flt(
        sum(abs(flt(row.amount, 3)) for row in (original.payments or [])),
        3,
    )
    returned_paid = flt(
        sum(
            abs(flt(value, 3))
            for value in frappe.get_all(
                "Sales Invoice",
                filters={
                    "return_against": original_invoice,
                    "is_return": 1,
                    "docstatus": 1,
                },
                pluck="paid_amount",
            )
        ),
        3,
    )
    return max(flt(original_paid - returned_paid, 3), 0)


def _legacy_protected_refund_plan(original_invoice):
    protected = _legacy_protected_payments(original_invoice)
    if not protected:
        return None

    original = frappe.get_doc("Sales Invoice", original_invoice)
    remaining = _legacy_remaining_paid_amount(original_invoice)
    enabled, cash_mode = _cash_override_config()
    channels = sorted({row["channel"] for row in protected})
    modes = sorted({row["mode_of_payment"] for row in protected})
    label = ", ".join(modes)

    # Legacy invoices do not have a trustworthy provider/source allocation. Keep the
    # UI controlled by Payment Hub, but require a manager-authorized Cash override.
    source = {
        "source_allocation": f"LEGACY:{original_invoice}",
        "legacy_source": True,
        "sequence": 1,
        "channel": channels[0] if len(channels) == 1 else "Electronic Payment",
        "mode_of_payment": label or "Electronic Payment",
        "amount": remaining,
        "refunded_amount": 0.0,
        "refundable_amount": remaining,
        "currency": original.currency,
        "gateway_transaction": None,
        "provider_account": None,
        "provider": "Legacy / Untracked",
        "actual_payment_method": None,
        "refund_supported": False,
        "authorization_required": True,
        "override_allowed": bool(enabled),
        "override_targets": ([{
            "channel": "Cash",
            "mode_of_payment": cash_mode,
        }] if enabled else []),
        "warning": (
            "The original electronic/terminal payment is not linked to a Payment Hub "
            "provider transaction. Direct gateway/terminal refund is blocked. A manager "
            "with Refund Override permission must authorize a Cash refund."
        ),
    }
    return {
        "managed": False,
        "legacy_protected": True,
        "original_invoice": original_invoice,
        "session": None,
        "currency": original.currency,
        "sources": [source] if remaining > 0 else [],
        "total_refundable": remaining,
        "supported_refundable": 0.0,
        "suggested_refunds": [],
        "protected_payment_modes": modes,
        "requires_override": True,
    }


def _source_response(allocation, *, exclude_return_invoice=None):
    refunded = _source_refunded_amount(
        allocation, exclude_return_invoice=exclude_return_invoice
    )
    amount = flt(allocation.amount, 3)
    refundable = max(flt(amount - refunded, 3), 0)
    account_status = _provider_account_status(allocation.provider_account)

    refund_supported = True
    warning = None
    if allocation.channel == "Electronic Payment":
        if not allocation.gateway_transaction:
            refund_supported = False
            warning = "Original gateway transaction is missing."
        elif account_status == "Disabled":
            refund_supported = False
            warning = (
                f"Provider account {allocation.provider_account} is Disabled. "
                "Set it to Refund Only, Active or Test before refunding old payments."
            )
    elif allocation.channel == "Physical Payment Terminal":
        refund_supported = False
        warning = "Physical terminal refund adapter is not enabled yet."

    return {
        "source_allocation": allocation.name,
        "sequence": allocation.sequence,
        "channel": allocation.channel,
        "mode_of_payment": allocation.mode_of_payment,
        "amount": amount,
        "refunded_amount": refunded,
        "refundable_amount": refundable,
        "currency": allocation.currency,
        "gateway_transaction": allocation.gateway_transaction,
        "provider_account": allocation.provider_account,
        "provider": allocation.provider,
        "actual_payment_method": allocation.actual_payment_method,
        "refund_supported": refund_supported,
        "authorization_required": _authorization_required_for_channel(allocation.channel),
        "override_allowed": bool(_cash_override_config()[0] and allocation.channel != "Cash"),
        "override_targets": ([{
            "channel": "Cash",
            "mode_of_payment": _cash_override_config()[1],
        }] if _cash_override_config()[0] and allocation.channel != "Cash" else []),
        "warning": warning,
    }


def _refund_allocation_response(row):
    row.reload()
    gateway_status = None
    provider_refund_id = None
    if row.refund_gateway_transaction and frappe.db.exists(
        "Gateway Transaction", row.refund_gateway_transaction
    ):
        txn = frappe.get_doc("Gateway Transaction", row.refund_gateway_transaction)
        gateway_status = txn.status
        provider_refund_id = txn.provider_refund_id

    return {
        "name": row.name,
        "original_invoice": row.original_invoice,
        "return_invoice": row.return_invoice,
        "source_allocation": row.source_allocation,
        "source_gateway_transaction": row.source_gateway_transaction,
        "refund_gateway_transaction": row.refund_gateway_transaction,
        "channel": row.channel,
        "mode_of_payment": row.mode_of_payment,
        "provider_account": row.provider_account,
        "provider": row.provider,
        "actual_payment_method": row.actual_payment_method,
        "amount": flt(row.amount, 3),
        "currency": row.currency,
        "status": row.status,
        "provider_status": row.provider_status or gateway_status,
        "provider_refund_id": provider_refund_id,
        "actual_refund_channel": row.actual_refund_channel or row.channel,
        "actual_refund_mode_of_payment": row.actual_refund_mode_of_payment or row.mode_of_payment,
        "is_override": bool(row.is_override),
        "authorization": row.authorization,
        "authorized_by": row.authorized_by,
        "initiated_by": row.initiated_by,
        "completed_at": row.completed_at,
        "override_reason": row.override_reason,
        "reason": row.reason,
        "error": row.error,
    }


def _status_from_gateway(gateway_status):
    if gateway_status == "Refunded":
        return "Completed"
    if gateway_status == "Failed":
        return "Failed"
    return "Pending"


def sync_refund_allocation_from_gateway(gateway_doc):
    if gateway_doc.transaction_type != "Refund":
        return None

    name = frappe.db.get_value(
        "POS Refund Allocation",
        {"refund_gateway_transaction": gateway_doc.name},
        "name",
    )
    if not name:
        return None

    row = frappe.get_doc("POS Refund Allocation", name)
    row.provider_status = gateway_doc.status
    row.status = _status_from_gateway(gateway_doc.status)
    if row.status == "Completed":
        row.error = None
        if not row.completed_at:
            row.completed_at = now_datetime()
    row.save(ignore_permissions=True)
    return row


@frappe.whitelist()
def get_refund_plan(original_invoice, refund_amount=None):
    if not frappe.db.exists("Sales Invoice", original_invoice):
        frappe.throw(f"Sales Invoice {original_invoice} does not exist.")

    session = _get_payment_hub_session(original_invoice)
    if not session:
        legacy_plan = _legacy_protected_refund_plan(original_invoice)
        if legacy_plan:
            return legacy_plan
        return {
            "managed": False,
            "legacy_protected": False,
            "original_invoice": original_invoice,
            "session": None,
            "currency": frappe.db.get_value("Sales Invoice", original_invoice, "currency"),
            "sources": [],
            "total_refundable": 0.0,
            "suggested_refunds": [],
        }

    allocation_names = frappe.get_all(
        "POS Payment Allocation",
        filters={"session": session.name, "status": ["in", ["Captured", "Refunded"]]},
        pluck="name",
        order_by="sequence asc, creation asc",
    )
    sources = [
        _source_response(frappe.get_doc("POS Payment Allocation", name))
        for name in allocation_names
    ]
    sources = [row for row in sources if row["refundable_amount"] > 0]

    total_refundable = flt(sum(row["refundable_amount"] for row in sources), 3)
    supported_refundable = flt(
        sum(row["refundable_amount"] for row in sources if row["refund_supported"]),
        3,
    )

    requested = flt(refund_amount or 0, 3)
    suggested = []
    if requested > 0:
        if requested > total_refundable + 0.0005:
            frappe.throw(
                f"Maximum refundable amount is {total_refundable:.3f} {session.currency}."
            )
        remaining = requested
        for source in sources:
            if remaining <= 0:
                break
            amount = min(flt(source["refundable_amount"], 3), remaining)
            if amount > 0:
                suggested.append(
                    {
                        "source_allocation": source["source_allocation"],
                        "amount": flt(amount, 3),
                    }
                )
                remaining = flt(remaining - amount, 3)

    return {
        "managed": True,
        "original_invoice": original_invoice,
        "session": session.name,
        "currency": session.currency,
        "sources": sources,
        "total_refundable": total_refundable,
        "supported_refundable": supported_refundable,
        "suggested_refunds": suggested,
    }


def _validate_return_invoice(original_invoice, return_invoice):
    doc = frappe.get_doc("Sales Invoice", return_invoice)
    if doc.docstatus != 0:
        frappe.throw(f"Return invoice {return_invoice} must be a Draft before refunding.")
    if not doc.is_return or doc.return_against != original_invoice:
        frappe.throw(
            f"{return_invoice} is not a return draft against {original_invoice}."
        )
    return doc


def _refund_target_from_draft(return_doc):
    return flt(sum(abs(flt(row.amount, 3)) for row in (return_doc.payments or [])), 3)


def _draft_payment_totals(return_doc):
    totals = {}
    for row in return_doc.payments or []:
        mode = row.mode_of_payment
        totals[mode] = flt(totals.get(mode, 0) + abs(flt(row.amount, 3)), 3)
    return totals


def _validate_refund_line_payment_modes(return_doc, parsed_lines):
    expected = {}
    for source_name, amount, override_channel, override_mode, _override_reason in parsed_lines:
        mode = override_mode or frappe.db.get_value(
            "POS Payment Allocation", source_name, "mode_of_payment"
        )
        expected[mode] = flt(expected.get(mode, 0) + flt(amount, 3), 3)
    actual = _draft_payment_totals(return_doc)
    if set(actual) != set(expected):
        frappe.throw(
            "Return invoice payment methods do not match the authorized refund sources."
        )
    for mode, amount in expected.items():
        if abs(flt(actual.get(mode), 3) - amount) > 0.0005:
            frappe.throw(
                f"Return invoice payment amount for {mode} does not match the refund allocation."
            )


def _existing_refund_row(return_invoice, source_allocation):
    name = frappe.db.get_value(
        "POS Refund Allocation",
        {
            "return_invoice": return_invoice,
            "source_allocation": source_allocation,
        },
        "name",
    )
    return frappe.get_doc("POS Refund Allocation", name) if name else None


def _create_refund_row(
    original_invoice,
    return_invoice,
    allocation,
    amount,
    reason,
    *,
    actual_refund_channel=None,
    actual_refund_mode_of_payment=None,
    authorization=None,
    is_override=False,
    override_reason=None,
):
    row = frappe.new_doc("POS Refund Allocation")
    row.original_invoice = original_invoice
    row.return_invoice = return_invoice
    row.source_allocation = allocation.name
    row.source_gateway_transaction = allocation.gateway_transaction
    row.channel = allocation.channel
    row.mode_of_payment = allocation.mode_of_payment
    row.amount = flt(amount, 3)
    row.currency = allocation.currency
    row.provider_account = allocation.provider_account
    row.provider = allocation.provider
    row.actual_payment_method = allocation.actual_payment_method
    row.actual_refund_channel = actual_refund_channel or allocation.channel
    row.actual_refund_mode_of_payment = actual_refund_mode_of_payment or allocation.mode_of_payment
    row.is_override = 1 if is_override else 0
    row.authorization = authorization.name if authorization else None
    row.authorized_by = authorization.authorized_by if authorization else None
    row.initiated_by = frappe.session.user
    row.override_reason = override_reason if is_override else None
    row.status = "Reserved"
    row.idempotency_key = f"RETURN:{return_invoice}:{allocation.name}"
    row.reason = reason
    row.insert(ignore_permissions=True)
    return row



def _find_refund_gateway_transaction(original_transaction, return_invoice, amount):
    rows = frappe.get_all(
        "Gateway Transaction",
        filters={
            "transaction_type": "Refund",
            "original_transaction": original_transaction,
            "reference_doctype": "Sales Invoice",
            "reference_name": return_invoice,
        },
        fields=["name", "amount"],
        order_by="creation asc",
    )
    for row in rows:
        if abs(flt(row.amount, 3) - flt(amount, 3)) <= 0.0005:
            return frappe.get_doc("Gateway Transaction", row.name)
    return None

def _refresh_refund_row(row):
    if not row.refund_gateway_transaction:
        return row
    from erpnext_payment_hub.api import refresh_transaction

    result = refresh_transaction(row.refund_gateway_transaction)
    row.reload()
    row.provider_status = result.get("status")
    row.status = _status_from_gateway(result.get("status"))
    if row.status == "Completed":
        row.error = None
    row.save(ignore_permissions=True)
    return row


@frappe.whitelist()
def process_pos_return_refund(
    original_invoice,
    return_invoice,
    refund_lines,
    reason=None,
    authorization_token=None,
):
    return_doc = _validate_return_invoice(original_invoice, return_invoice)
    lines = _as_list(refund_lines)
    if not lines:
        frappe.throw("Refund lines are required for a Payment Hub return.")

    session = _get_payment_hub_session(original_invoice)
    if not session:
        frappe.throw(
            f"Sales Invoice {original_invoice} is not linked to a Payment Hub session."
        )

    target = _refund_target_from_draft(return_doc)
    requested_total = flt(sum(flt(line.get("amount"), 3) for line in lines), 3)
    if abs(requested_total - target) > 0.0005:
        frappe.throw(
            f"Refund allocation total {requested_total:.3f} must equal the draft "
            f"refund payment total {target:.3f} {return_doc.currency}."
        )

    # Lock original allocation rows in deterministic order, validate all sources,
    # and persist ALL refund reservations before making the first external call.
    # This prevents two simultaneous returns from both seeing the same refundable
    # balance and also prevents a split refund from being only partly reserved.
    parsed_lines = []
    seen = set()
    override_requested = False
    override_channel = None
    override_mode_of_payment = None
    for line in lines:
        source_name = line.get("source_allocation")
        amount = flt(line.get("amount"), 3)
        line_override_channel = line.get("override_channel")
        line_override_mode = line.get("override_mode_of_payment")
        if not source_name or amount <= 0:
            frappe.throw("Each refund line requires a source allocation and amount > 0.")
        if source_name in seen:
            frappe.throw(f"Duplicate refund source {source_name}.")
        seen.add(source_name)
        if line_override_channel or line_override_mode:
            enabled, cash_mode = _cash_override_config()
            if not enabled:
                frappe.throw("Refund method override is disabled in Payment Hub Settings.")
            line_override_channel = line_override_channel or "Cash"
            line_override_mode = line_override_mode or cash_mode
            if line_override_channel != "Cash" or line_override_mode != cash_mode:
                frappe.throw(
                    "v0.5.0 only permits an audited manager override to the configured Cash refund method."
                )
            override_requested = True
            if override_channel and (override_channel != line_override_channel or override_mode_of_payment != line_override_mode):
                frappe.throw("All refund overrides in one return must use the same authorized target.")
            override_channel = line_override_channel
            override_mode_of_payment = line_override_mode
        parsed_lines.append((source_name, amount, line_override_channel, line_override_mode, line.get("override_reason")))
    parsed_lines.sort(key=lambda item: item[0])
    _validate_refund_line_payment_modes(return_doc, parsed_lines)

    # Determine whether this return needs manager/admin authorization before any
    # provider side effect. Authorization is bound to this draft return and amount.
    source_channels = {}
    for source_name, _amount, _oc, _om, _or in parsed_lines:
        source_channels[source_name] = frappe.db.get_value("POS Payment Allocation", source_name, "channel")
    authorization_required = any(
        _authorization_required_for_channel(channel) for channel in source_channels.values()
    )
    authorization = None
    if authorization_required or override_requested:
        authorization = validate_refund_authorization(
            authorization_token,
            original_invoice=original_invoice,
            return_invoice=return_invoice,
            amount=requested_total,
            source_allocations=[item[0] for item in parsed_lines],
            requires_override=override_requested,
            override_channel=override_channel,
            override_mode_of_payment=override_mode_of_payment,
        )

    allocations = {}
    validation_errors = []
    for source_name, amount, line_override_channel, line_override_mode, line_override_reason in parsed_lines:
        frappe.db.sql(
            "SELECT name FROM `tabPOS Payment Allocation` WHERE name=%s FOR UPDATE",
            (source_name,),
        )
        allocation = frappe.get_doc("POS Payment Allocation", source_name)
        if allocation.session != session.name or allocation.status not in ("Captured", "Refunded"):
            frappe.throw(f"{source_name} is not a captured payment for {original_invoice}.")

        source = _source_response(
            allocation, exclude_return_invoice=return_invoice
        )
        existing = _existing_refund_row(return_invoice, source_name)
        existing_amount = flt(existing.amount, 3) if existing else 0
        available_for_this_return = flt(source["refundable_amount"] + existing_amount, 3)

        if amount > available_for_this_return + 0.0005:
            frappe.throw(
                f"Maximum refundable amount for {source_name} is "
                f"{available_for_this_return:.3f} {allocation.currency}."
            )
        is_override = bool(line_override_channel or line_override_mode)
        if not source["refund_supported"] and not is_override:
            validation_errors.append(source["warning"] or f"Refund not supported for {source_name}.")

        actual_channel = line_override_channel or allocation.channel
        actual_mode = line_override_mode or allocation.mode_of_payment
        if existing:
            if abs(flt(existing.amount, 3) - amount) > 0.0005:
                frappe.throw(
                    f"Existing refund reservation {existing.name} has amount "
                    f"{flt(existing.amount,3):.3f}; it cannot be changed after processing started."
                )
            if (existing.actual_refund_channel or existing.channel) != actual_channel or (existing.actual_refund_mode_of_payment or existing.mode_of_payment) != actual_mode:
                frappe.throw("Refund method cannot be changed after processing has started.")
            row = existing
        else:
            row = _create_refund_row(
                original_invoice,
                return_invoice,
                allocation,
                amount,
                reason,
                actual_refund_channel=actual_channel,
                actual_refund_mode_of_payment=actual_mode,
                authorization=authorization,
                is_override=is_override,
                override_reason=line_override_reason or reason,
            )
        allocations[source_name] = (allocation, amount, row)

    if validation_errors:
        frappe.throw("\n".join(validation_errors))

    # The reservation commit is deliberate: provider refunds are external side
    # effects and cannot be rolled back with the request transaction.
    frappe.db.commit()
    if authorization:
        mark_authorization_used(authorization)
        frappe.db.commit()

    results = []
    for source_name, (allocation, amount, row) in allocations.items():
        row.reload()
        if row.status == "Completed":
            results.append(_refund_allocation_response(row))
            continue
        if row.refund_gateway_transaction:
            try:
                row = _refresh_refund_row(row)
            except Exception:
                row.reload()
            results.append(_refund_allocation_response(row))
            continue
        if row.status in ("Processing", "Manual Review"):
            # A reservation without a gateway transaction means a previous external
            # request may have failed after leaving ERPNext. Never auto-repeat it.
            row.status = "Manual Review"
            row.error = row.error or (
                "Refund request was started previously but no gateway transaction "
                "was recorded. Review the provider before retrying."
            )
            row.save(ignore_permissions=True)
            frappe.db.commit()
            results.append(_refund_allocation_response(row))
            continue

        if (row.actual_refund_channel or allocation.channel) == "Cash":
            row.status = "Completed"
            row.provider_status = "Cash Refund Override" if row.is_override else "Cash Refunded"
            if not row.completed_at:
                row.completed_at = now_datetime()
            row.save(ignore_permissions=True)
            frappe.db.commit()
            results.append(_refund_allocation_response(row))
            continue

        row.status = "Processing"
        row.save(ignore_permissions=True)
        frappe.db.commit()

        try:
            from erpnext_payment_hub.api import refund_transaction

            refund_result = refund_transaction(
                allocation.gateway_transaction,
                amount,
                reason=reason,
                reference_doctype="Sales Invoice",
                reference_name=return_invoice,
            )
            refund_name = refund_result.get("refund_transaction")
            refund_txn = frappe.get_doc("Gateway Transaction", refund_name)
            row.refund_gateway_transaction = refund_name
            row.provider_status = refund_txn.status
            row.status = _status_from_gateway(refund_txn.status)
            row.error = None if row.status != "Failed" else "Provider refund failed."
            if row.status == "Completed" and not row.completed_at:
                row.completed_at = now_datetime()
            row.save(ignore_permissions=True)
            frappe.db.commit()
        except Exception as exc:
            # If the provider call succeeded but a later local save failed, recover
            # the Gateway Transaction by the return-invoice idempotency anchor.
            recovered = _find_refund_gateway_transaction(
                allocation.gateway_transaction, return_invoice, amount
            )
            row.reload()
            if recovered:
                row.refund_gateway_transaction = recovered.name
                row.provider_status = recovered.status
                row.status = _status_from_gateway(recovered.status)
                row.error = None if row.status != "Failed" else str(exc)[:1000]
                if row.status == "Completed" and not row.completed_at:
                    row.completed_at = now_datetime()
            else:
                # External POSTs cannot be rolled back. Do not retry automatically
                # when the provider result is uncertain; leave a durable review marker.
                row.status = "Manual Review"
                row.error = str(exc)[:1000]
            row.save(ignore_permissions=True)
            frappe.db.commit()

        results.append(_refund_allocation_response(row))

    return get_return_refund_status(return_invoice)


def _matching_legacy_override_authorization(return_doc, amount):
    enabled, cash_mode = _cash_override_config()
    if not enabled:
        frappe.throw("Refund method override is disabled in Payment Hub Settings.", frappe.PermissionError)

    requester = return_doc.owner or frappe.session.user
    rows = frappe.get_all(
        "Payment Hub Refund Authorization",
        filters={
            "original_invoice": return_doc.return_against,
            "return_invoice": return_doc.name,
            "cashier_user": requester,
            "action": "Refund Override",
            "is_override": 1,
            "override_channel": "Cash",
            "override_mode_of_payment": cash_mode,
            "status": ["in", ["Valid", "Used"]],
        },
        fields=["name", "status", "amount", "expires_at", "authorized_by", "used_at"],
        order_by="creation desc",
    )
    now = now_datetime()
    for row in rows:
        if flt(row.amount, 3) + 0.0005 < flt(amount, 3):
            continue
        if row.status == "Valid" and row.expires_at and row.expires_at < now:
            frappe.db.set_value("Payment Hub Refund Authorization", row.name, "status", "Expired")
            continue
        return row
    return None


def validate_sales_invoice_return_security(doc, method=None):
    """Server-side fail-closed guard for POS electronic/terminal returns.

    Frontend controls are convenience only. This hook prevents direct calls to
    POSNext/ERPNext submit APIs from bypassing Payment Hub refund authorization.
    """
    if doc.doctype != "Sales Invoice" or not cint(doc.is_return) or not doc.return_against:
        return
    if not frappe.db.exists("Sales Invoice", doc.return_against):
        return

    protected = _legacy_protected_payments(doc.return_against)
    if not protected:
        return

    # Customer-credit returns contain no payment rows and do not release cash/card funds.
    refund_amount = flt(sum(abs(flt(row.amount, 3)) for row in (doc.payments or [])), 3)
    if refund_amount <= 0:
        return

    session = _get_payment_hub_session(doc.return_against)
    if session:
        allocation_names = frappe.get_all(
            "POS Refund Allocation",
            filters={"return_invoice": doc.name},
            pluck="name",
        )
        if not allocation_names:
            frappe.throw(
                "Electronic/terminal return is controlled by Payment Hub. Process the refund "
                "and manager authorization in POS before submitting this return invoice.",
                frappe.PermissionError,
            )

        rows = [frappe.get_doc("POS Refund Allocation", name) for name in allocation_names]
        if any(row.status != "Completed" for row in rows):
            frappe.throw(
                "Payment Hub refund is not completed. The return invoice cannot be submitted yet.",
                frappe.PermissionError,
            )
        for row in rows:
            if _authorization_required_for_channel(row.channel) or cint(row.is_override):
                if not row.authorization or not row.authorized_by:
                    frappe.throw(
                        "Manager/admin authorization is missing for this electronic/terminal refund.",
                        frappe.PermissionError,
                    )
        return

    # Legacy/non-tracked electronic or terminal payment. We cannot safely claim a
    # provider refund because there is no Payment Hub source transaction. Only the
    # configured Cash override is allowed, and it always requires manager password.
    _enabled, cash_mode = _cash_override_config()
    actual_modes = {row.mode_of_payment for row in (doc.payments or []) if abs(flt(row.amount, 3)) > 0}
    if actual_modes != {cash_mode}:
        frappe.throw(
            "This original Electronic/Physical payment is not linked to a Payment Hub provider "
            "transaction. Direct electronic/terminal refund is blocked. Use a manager-authorized "
            f"override to {cash_mode}.",
            frappe.PermissionError,
        )

    authorization = _matching_legacy_override_authorization(doc, refund_amount)
    if not authorization:
        frappe.throw(
            "Manager password authorization with Payment Hub Refund Override permission is "
            "required before this Cash override can be submitted.",
            frappe.PermissionError,
        )

    # Marking Used is in the same DB transaction as Sales Invoice submission, so a
    # later submit failure rolls this state back as well. A Used authorization remains
    # bound to this exact return invoice and cannot authorize another return.
    if authorization.status == "Valid":
        frappe.db.set_value(
            "Payment Hub Refund Authorization",
            authorization.name,
            {"status": "Used", "used_at": now_datetime()},
        )


@frappe.whitelist()
def get_return_refund_status(return_invoice):
    rows = frappe.get_all(
        "POS Refund Allocation",
        filters={"return_invoice": return_invoice},
        pluck="name",
        order_by="creation asc",
    )
    if not rows:
        return {
            "managed": False,
            "return_invoice": return_invoice,
            "all_complete": False,
            "rows": [],
        }

    result_rows = [
        _refund_allocation_response(frappe.get_doc("POS Refund Allocation", name))
        for name in rows
    ]
    return {
        "managed": True,
        "return_invoice": return_invoice,
        "original_invoice": result_rows[0]["original_invoice"],
        "all_complete": all(row["status"] == "Completed" for row in result_rows),
        "has_pending": any(
            row["status"] in ("Reserved", "Processing", "Pending")
            for row in result_rows
        ),
        "needs_review": any(
            row["status"] in ("Failed", "Manual Review") for row in result_rows
        ),
        "rows": result_rows,
    }


@frappe.whitelist()
def refresh_return_refunds(return_invoice):
    names = frappe.get_all(
        "POS Refund Allocation",
        filters={"return_invoice": return_invoice},
        pluck="name",
        order_by="creation asc",
    )
    for name in names:
        row = frappe.get_doc("POS Refund Allocation", name)
        if row.status in ("Pending", "Processing") and row.refund_gateway_transaction:
            try:
                _refresh_refund_row(row)
            except Exception:
                frappe.log_error(
                    title=f"Payment Hub return refund refresh failed: {row.name}",
                    message=frappe.get_traceback(),
                )
    return get_return_refund_status(return_invoice)

"""Separate an accepted provider refund instruction from customer settlement."""
from __future__ import annotations

import json
from html import escape

import frappe
from frappe.utils import cint, flt


def _pending_accepted(row):
    if (
        row.status != "Pending" or row.provider != "UPayments"
        or row.channel != "Electronic Payment" or cint(row.is_override)
        or not row.refund_gateway_transaction
    ):
        return False
    txn = frappe.get_doc("Gateway Transaction", row.refund_gateway_transaction)
    if (
        txn.transaction_type != "Refund" or txn.provider != row.provider
        or txn.provider_account != row.provider_account
        or txn.original_transaction != row.source_gateway_transaction
        or txn.reference_doctype != "Sales Invoice" or txn.reference_name != row.return_invoice
        or txn.status != "Pending" or txn.currency != row.currency
        or abs(flt(txn.amount, 3) - flt(row.amount, 3)) > 0.0005
        or not txn.provider_refund_id or not txn.provider_order_id
    ):
        return False
    try:
        raw = json.loads(txn.response_json or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(raw, dict) or raw.get("status") is not True:
        return False
    data = raw.get("data") or {}
    if not isinstance(data, dict):
        return False
    # Acceptance response or a later uniquely matched status inquiry.
    if (
        str(data.get("refundOrderId") or "") == str(txn.provider_refund_id)
        and str(data.get("orderId") or "") == str(txn.provider_order_id)
    ):
        return True
    matches = [
        item for item in data.get("refund", [])
        if isinstance(item, dict)
        and str(item.get("order_id") or "") == str(txn.provider_order_id)
    ] if isinstance(data.get("refund"), list) else []
    return bool(
        len(matches) == 1
        and str(matches[0].get("status") or "").upper() in {
            "PENDING", "PROCESSING", "INITIATED", "REQUESTED", "APPROVED"
        }
        and matches[0].get("is_refunded") is False
    )


def _source_matches(row, doc):
    if (
        row.return_invoice != doc.name or row.original_invoice != doc.return_against
        or row.currency != doc.currency or flt(row.amount, 3) <= 0
    ):
        return False
    source = frappe.get_doc("POS Payment Allocation", row.source_allocation)
    if (
        source.status not in ("Captured", "Refunded") or source.currency != row.currency
        or source.gateway_transaction != row.source_gateway_transaction
        or source.provider_account != row.provider_account
        or flt(row.amount, 3) > flt(source.amount, 3) + 0.0005
    ):
        return False
    if not source.session:
        return False
    session = frappe.get_doc("POS Payment Session", source.session)
    if session.invoice_name != doc.return_against or session.company != doc.company or session.customer != doc.customer:
        return False
    if source.gateway_transaction:
        original = frappe.get_doc("Gateway Transaction", source.gateway_transaction)
        if (
            original.transaction_type != "Payment" or original.status not in ("Captured", "Refunded")
            or original.provider_account != row.provider_account
        ):
            return False
    return True


def rows_ready(doc, rows):
    if not rows or doc.docstatus not in (0, 1) or not cint(doc.is_return) or not doc.return_against:
        return False
    expected = {}
    for row in rows:
        if not _source_matches(row, doc) or not (row.status == "Completed" or _pending_accepted(row)):
            return False
        mode = row.actual_refund_mode_of_payment or row.mode_of_payment
        expected[mode] = flt(expected.get(mode, 0) + flt(row.amount, 3), 3)
    actual = {}
    for tender in doc.payments or []:
        amount = flt(tender.amount, 3)
        if amount > 0:
            return False
        if amount:
            actual[tender.mode_of_payment] = flt(actual.get(tender.mode_of_payment, 0) + abs(amount), 3)
    return set(expected) == set(actual) and all(
        abs(expected[mode] - actual[mode]) <= 0.0005 for mode in expected
    )


def validate_ready(doc, rows):
    """Called inside before_submit; retains authorization and exact tender coverage."""
    from erpnext_payment_hub.pos.refund import (
        _authorization_required_for_channel, _validate_persisted_retry_authorization,
    )
    if not rows_ready(doc, rows):
        frappe.throw(
            "Return submission requires completed refunds or a verified accepted UPayments refund "
            "for every payment row. Rejected or uncertain requests remain Draft.",
            frappe.PermissionError,
        )
    for row in rows:
        if _authorization_required_for_channel(row.channel) or cint(row.is_override):
            _validate_persisted_retry_authorization(row)
        if _pending_accepted(row):
            # Do not let the POS API's generic Cash fallback post an online refund.
            mode = row.actual_refund_mode_of_payment or row.mode_of_payment
            account = frappe.db.get_value(
                "Mode of Payment Account", {"parent": mode, "company": doc.company}, "default_account"
            )
            if not account or frappe.db.get_value("Account", account, "account_type") == "Cash":
                frappe.throw("Configure an explicit non-Cash account for the original online refund mode before submitting.")
            if any(p.mode_of_payment == mode and p.account != account for p in doc.payments or []):
                frappe.throw("Return payment account does not match the configured original refund mode.")


def submission_summary(doc, rows):
    completed = flt(sum(flt(row.amount, 3) for row in rows if row.status == "Completed"), 3)
    processing = flt(sum(flt(row.amount, 3) for row in rows if _pending_accepted(row)), 3)
    ready = rows_ready(doc, rows)
    return {
        "can_submit_return": bool(doc.docstatus == 0 and ready),
        "refund_processing_amount": processing,
        "refund_completed_amount": completed,
        "return_docstatus": doc.docstatus,
    }


def receipt_html(doc, rows):
    """An explicit settlement notice, separate from the invoice tender/GL audit."""
    if not rows:
        return ""
    summary = submission_summary(doc, rows)
    complete = all(row.status == "Completed" for row in rows)
    heading = "Refund completed" if complete else "Refund requested - processing" if summary["refund_processing_amount"] else "Refund requires attention"
    lines = [heading]
    if not complete:
        lines.append("Bank credit is not yet confirmed. Recorded payment rows represent the refund instruction, not proof of bank settlement.")
    lines.append(f"Refund completed: {doc.currency} {summary['refund_completed_amount']:.3f}")
    lines.append(f"Refund processing: {doc.currency} {summary['refund_processing_amount']:.3f}")
    for row in rows:
        line = f"{row.actual_refund_mode_of_payment or row.mode_of_payment}: {row.currency} {flt(row.amount, 3):.3f} - {row.status}"
        if row.refund_gateway_transaction:
            txn = frappe.get_doc("Gateway Transaction", row.refund_gateway_transaction)
            if txn.provider_refund_id:
                line += f" | Provider refund ID: {txn.provider_refund_id}"
            if getattr(row, "provider_reference", None):
                line += f" | Reference / ARN: {row.provider_reference}"
        lines.append(line)
    return (
        '<section class="payment-hub-refund-notice" style="border:2px solid #333;padding:8px;margin:6px 0;font-size:11px;'
        'line-height:1.4;overflow-wrap:anywhere;break-inside:avoid">'
        + "<br>".join(escape(line) for line in lines) + "</section>"
    )


def invoice_receipt_html(doc):
    names = frappe.get_all("POS Refund Allocation", filters={"return_invoice": doc.name}, pluck="name")
    return receipt_html(doc, [frappe.get_doc("POS Refund Allocation", name) for name in names])

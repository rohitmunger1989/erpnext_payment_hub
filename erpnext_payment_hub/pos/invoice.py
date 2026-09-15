from __future__ import annotations

import json
from urllib.parse import quote

import frappe
from frappe.utils import flt

from erpnext_payment_hub.pos.service import allocation_rows, get_settings


def _parse_payload(value):
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    frappe.throw("Invoice payload must be a JSON object.")


def _extract_invoice_payload(payload):
    payload = _parse_payload(payload)
    if not payload:
        return None
    if isinstance(payload.get("invoice"), dict):
        return payload["invoice"]
    if isinstance(payload.get("doc"), dict):
        return payload["doc"]
    return payload


def captured_payment_rows(session_name):
    totals = {}
    for row in allocation_rows(session_name):
        if row.status != "Captured":
            continue
        mode = row.mode_of_payment or row.channel
        totals[mode] = flt(totals.get(mode), 3) + flt(row.amount, 3)
    return [
        {"mode_of_payment": mode, "amount": flt(amount, 3)}
        for mode, amount in totals.items()
    ]


def _validate_modes(rows):
    missing = [
        row["mode_of_payment"]
        for row in rows
        if row.get("mode_of_payment")
        and not frappe.db.exists("Mode of Payment", row["mode_of_payment"])
    ]
    if missing:
        frappe.throw(
            "Create these ERPNext Mode of Payment records before completing the sale: "
            + ", ".join(sorted(set(missing)))
        )


def _set_invoice_payments(doc, payment_rows):
    meta = frappe.get_meta(doc.doctype)
    if not meta.has_field("payments"):
        return False

    _validate_modes(payment_rows)
    doc.set("payments", [])
    for row in payment_rows:
        child = doc.append("payments", {})
        child.mode_of_payment = row["mode_of_payment"]
        child.amount = flt(row["amount"], 3)
    return True


def _validate_total(doc, session):
    if not hasattr(doc, "grand_total"):
        return
    invoice_total = flt(doc.grand_total, 3)
    session_total = flt(session.grand_total, 3)
    if abs(invoice_total - session_total) > 0.001:
        frappe.throw(
            f"Invoice total {invoice_total:.3f} {session.currency} does not match "
            f"POS Payment Session total {session_total:.3f} {session.currency}."
        )



def resolve_draft_print_format(print_format=None, print_type=None):
    """Resolve A4/receipt draft printing without hardcoding a site print format."""
    settings = get_settings()
    requested = str(print_type or getattr(settings, "default_draft_print_type", None) or "Receipt").strip()
    if requested not in {"Receipt", "A4", "Ask Each Time"}:
        requested = "Receipt"
    if requested == "Ask Each Time":
        requested = "Receipt"

    if print_format:
        return print_format, requested

    if requested == "A4":
        resolved = getattr(settings, "draft_a4_print_format", None)
    else:
        resolved = getattr(settings, "draft_receipt_print_format", None)

    resolved = resolved or getattr(settings, "default_print_format", None) or "Standard"
    return resolved, requested

def build_print_result(doc, print_format=None):
    settings = get_settings()
    print_format = print_format or getattr(settings, "default_print_format", None) or "Standard"
    base = frappe.utils.get_url()
    doctype = quote(doc.doctype, safe="")
    name = quote(doc.name, safe="")
    fmt = quote(print_format, safe="")
    return {
        "doctype": doc.doctype,
        "name": doc.name,
        "docstatus": doc.docstatus,
        "submitted": doc.docstatus == 1,
        "print_format": print_format,
        "print_route": ["print", doc.doctype, doc.name],
        "print_url": f"{base}/printview?doctype={doctype}&name={name}&format={fmt}",
    }


def _invoice_claimed_by_other_session(session, invoice_doctype, invoice_name):
    """Return another POS Payment Session that already owns this invoice.

    A Sales Invoice draft is part of a single payment session.  Reusing the same
    draft from another cart can attach the wrong captured payment to the invoice.
    """
    if not invoice_name:
        return None
    filters = {
        "invoice_doctype": invoice_doctype,
        "invoice_name": invoice_name,
        "name": ["!=", session.name],
    }
    rows = frappe.get_all(
        "POS Payment Session",
        filters=filters,
        fields=["name", "status", "finalized", "grand_total", "cart_reference"],
        order_by="finalized desc, modified desc",
        limit=1,
    )
    return frappe._dict(rows[0]) if rows else None


def _assert_invoice_not_claimed_elsewhere(session, invoice_doctype, invoice_name):
    other = _invoice_claimed_by_other_session(session, invoice_doctype, invoice_name)
    if not other:
        return
    frappe.throw(
        f"{invoice_doctype} {invoice_name} is already linked to POS Payment Session "
        f"{other.name}. This session cannot reuse another cart's invoice. "
        "Create a fresh draft for this payment session."
    )


def _new_invoice_payload(payload, invoice_doctype):
    """Return a payload that is guaranteed to create a fresh draft.

    POS frontends may keep a stale ``name`` from an earlier draft in their cart
    state.  The invoice identity is owned by POS Payment Session, never by an
    untrusted/new-cart payload.  Once a session has ``invoice_name`` we reuse that
    explicit draft through ``invoice_name`` instead.
    """
    clean = dict(payload)
    clean["doctype"] = invoice_doctype
    for fieldname in (
        "name",
        "docstatus",
        "owner",
        "creation",
        "modified",
        "modified_by",
        "idx",
        "__islocal",
        "__unsaved",
    ):
        clean.pop(fieldname, None)
    return clean


def create_or_update_invoice(
    session,
    *,
    invoice_doctype="Sales Invoice",
    invoice_name=None,
    invoice_payload=None,
    submit=True,
    print_format=None,
    completion_pos_opening_shift=None,
):
    if invoice_name:
        if not frappe.db.exists(invoice_doctype, invoice_name):
            frappe.throw(f"{invoice_doctype} {invoice_name} does not exist.")
        _assert_invoice_not_claimed_elsewhere(session, invoice_doctype, invoice_name)
        doc = frappe.get_doc(invoice_doctype, invoice_name)
    else:
        payload = _extract_invoice_payload(invoice_payload or session.draft_payload)
        if not payload:
            frappe.throw(
                "No invoice payload is saved on this POS Payment Session. "
                "The POS adapter must save the cart/draft payload or pass invoice_payload."
            )
        # Never trust a new-cart payload to choose an existing invoice name.
        # The only supported reuse path is session.invoice_name / invoice_name.
        payload = _new_invoice_payload(payload, invoice_doctype)
        doc = frappe.get_doc(payload)

    if doc.docstatus == 2:
        frappe.throw(f"{doc.doctype} {doc.name} is cancelled.")

    rows = captured_payment_rows(session.name)
    if doc.docstatus == 0:
        meta = frappe.get_meta(doc.doctype)
        if meta.has_field("is_pos"):
            doc.is_pos = 1
        # The PPS keeps its original shift for audit.  If payment is captured
        # after that shift has closed, completion must post the invoice into the
        # caller's currently-open shift instead of reopening/mutating history.
        if completion_pos_opening_shift and meta.has_field("posa_pos_opening_shift"):
            doc.set("posa_pos_opening_shift", completion_pos_opening_shift)
        payments_attached = _set_invoice_payments(doc, rows)
        # Payment Hub stores only the cash amount applied to the invoice as a
        # payment allocation. Preserve the physical tender/change separately so
        # POS receipts can still show what the customer handed over.
        if frappe.get_meta(doc.doctype).has_field("change_amount"):
            doc.change_amount = flt(getattr(session, "change_amount", 0), 3)
        doc.save(ignore_permissions=True)
        _validate_total(doc, session)
        if submit:
            doc.submit()
    else:
        payments_attached = bool(frappe.get_meta(doc.doctype).has_field("payments"))
        _validate_total(doc, session)

    result = build_print_result(doc, print_format=print_format)
    result["payments_attached"] = payments_attached
    result["payment_rows"] = rows
    return doc, result

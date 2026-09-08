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
        "print_format": print_format,
        "print_route": ["print", doc.doctype, doc.name],
        "print_url": f"{base}/printview?doctype={doctype}&name={name}&format={fmt}",
    }


def create_or_update_invoice(
    session,
    *,
    invoice_doctype="Sales Invoice",
    invoice_name=None,
    invoice_payload=None,
    submit=True,
    print_format=None,
):
    if invoice_name:
        if not frappe.db.exists(invoice_doctype, invoice_name):
            frappe.throw(f"{invoice_doctype} {invoice_name} does not exist.")
        doc = frappe.get_doc(invoice_doctype, invoice_name)
    else:
        payload = _extract_invoice_payload(invoice_payload or session.draft_payload)
        if not payload:
            frappe.throw(
                "No invoice payload is saved on this POS Payment Session. "
                "The POS adapter must save the cart/draft payload or pass invoice_payload."
            )
        payload = dict(payload)
        payload.setdefault("doctype", invoice_doctype)
        doc = frappe.get_doc(payload)

    if doc.docstatus == 2:
        frappe.throw(f"{doc.doctype} {doc.name} is cancelled.")

    rows = captured_payment_rows(session.name)
    if doc.docstatus == 0:
        if frappe.get_meta(doc.doctype).has_field("is_pos"):
            doc.is_pos = 1
        payments_attached = _set_invoice_payments(doc, rows)
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

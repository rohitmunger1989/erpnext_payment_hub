from __future__ import annotations

import frappe

from erpnext_payment_hub.pos.service import get_settings


def _call_custom_sender(method, *, to, message, allocation, session):
    fn = frappe.get_attr(method)
    return fn(
        to=to,
        message=message,
        reference_doctype="POS Payment Allocation",
        reference_name=allocation.name,
        allocation=allocation.name,
        session=session.name,
    )


def _send_with_frappe_whatsapp(*, to, message, allocation, session, settings):
    if not frappe.db.exists("DocType", "WhatsApp Message"):
        frappe.throw(
            "Frappe WhatsApp is selected but WhatsApp Message DocType is not installed. "
            "Install/configure frappe_whatsapp or set Custom WhatsApp Sender Method."
        )

    doc = frappe.new_doc("WhatsApp Message")
    doc.type = "Outgoing"
    doc.to = to
    doc.content_type = "text"
    doc.reference_doctype = "POS Payment Allocation"
    doc.reference_name = allocation.name

    account = getattr(settings, "whatsapp_account_name", None)
    if account and doc.meta.has_field("whatsapp_account"):
        doc.whatsapp_account = account

    template = getattr(settings, "whatsapp_template_name", None)
    if template:
        if not frappe.db.exists("WhatsApp Templates", template):
            frappe.throw(f"WhatsApp template {template} does not exist.")
        doc.template = template
        doc.use_template = 1
        # frappe_whatsapp reads fields / dynamic URL values from the reference
        # POS Payment Allocation. The approved template can therefore reference
        # amount, currency, payment_url, mobile_number, etc.
    else:
        doc.message = message
        doc.message_type = "Manual"

    doc.insert(ignore_permissions=True)
    return {
        "integration": "Frappe WhatsApp",
        "message_name": doc.name,
        "message_id": getattr(doc, "message_id", None),
        "status": getattr(doc, "status", None),
        "template": template,
    }


def send_payment_message(*, allocation, session, message):
    settings = get_settings()
    method = (getattr(settings, "whatsapp_sender_method", None) or "").strip()
    if method:
        result = _call_custom_sender(
            method,
            to=allocation.mobile_number,
            message=message,
            allocation=allocation,
            session=session,
        )
        return {
            "integration": "Custom",
            "method": method,
            "result": result,
        }

    integration = getattr(settings, "whatsapp_integration", None) or "Frappe WhatsApp"

    # The installed frappe_whatsapp app itself uses Meta WhatsApp Cloud API, so
    # both choices can use its audited message DocType when it is available.
    if integration in ("Frappe WhatsApp", "Meta WhatsApp Cloud API"):
        return _send_with_frappe_whatsapp(
            to=allocation.mobile_number,
            message=message,
            allocation=allocation,
            session=session,
            settings=settings,
        )

    frappe.throw(f"Unsupported WhatsApp integration: {integration}")

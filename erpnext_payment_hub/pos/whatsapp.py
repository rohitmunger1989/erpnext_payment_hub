from __future__ import annotations

import inspect
import json

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


def _call_custom_refund_sender(method, *, to, message, refund_allocation, allocation=None, session=None):
    """Call an existing custom sender without breaking older exact signatures."""
    fn = frappe.get_attr(method)
    kwargs = {
        "to": to,
        "message": message,
        "reference_doctype": "POS Refund Allocation",
        "reference_name": refund_allocation.name,
        "refund_allocation": refund_allocation.name,
        "allocation": getattr(allocation, "name", None),
        "session": getattr(session, "name", None),
    }
    try:
        signature = inspect.signature(fn)
        if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
            kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
    except (TypeError, ValueError):
        pass
    return fn(**kwargs)


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


def _send_refund_with_frappe_whatsapp(*, to, message, refund_allocation, settings):
    if not frappe.db.exists("DocType", "WhatsApp Message"):
        frappe.throw(
            "Frappe WhatsApp is selected but WhatsApp Message DocType is not installed. "
            "Install/configure frappe_whatsapp or set Custom WhatsApp Sender Method."
        )

    doc = frappe.new_doc("WhatsApp Message")
    doc.type = "Outgoing"
    doc.to = to
    doc.content_type = "text"
    doc.reference_doctype = "POS Refund Allocation"
    doc.reference_name = refund_allocation.name

    account = getattr(settings, "whatsapp_account_name", None)
    if account and doc.meta.has_field("whatsapp_account"):
        doc.whatsapp_account = account

    template = (getattr(settings, "refund_whatsapp_template_name", None) or "").strip()
    if template:
        if not frappe.db.exists("WhatsApp Templates", template):
            frappe.throw(f"WhatsApp template {template} does not exist.")
        doc.template = template
        doc.use_template = 1
    else:
        doc.message = message
        doc.message_type = "Manual"

    doc.insert(ignore_permissions=True)
    return {
        "integration": "Frappe WhatsApp",
        "message_name": doc.name,
        "message_id": getattr(doc, "message_id", None),
        "status": getattr(doc, "status", None),
        "template": template or None,
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


def _json_dict(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _deep_find_first(value, keys):
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, "") and not isinstance(candidate, (dict, list)):
                return str(candidate)
        for child in value.values():
            found = _deep_find_first(child, keys)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _deep_find_first(child, keys)
            if found:
                return found
    return None


def provider_authorization_reference(transaction):
    """Best-effort provider authorization/auth code from the stored raw response."""
    if not transaction:
        return None
    raw = _json_dict(getattr(transaction, "response_json", None))
    return _deep_find_first(
        raw,
        (
            "authorization_code",
            "AuthorizationCode",
            "authorization_id",
            "AuthorizationId",
            "auth_code",
            "authCode",
            "approval_code",
            "approvalCode",
            "auth",
            "Auth",
        ),
    )



def _normalize_whatsapp_number(value):
    raw = str(value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return ""
    if digits.startswith("00965"):
        digits = digits[2:]
    elif len(digits) == 8:
        digits = f"965{digits}"
    return f"+{digits}"

def refund_customer_mobile(refund_allocation):
    """Resolve customer mobile from Payment Hub session, invoice, then Customer."""
    session = None
    allocation = None
    if refund_allocation.source_allocation and frappe.db.exists(
        "POS Payment Allocation", refund_allocation.source_allocation
    ):
        allocation = frappe.get_doc("POS Payment Allocation", refund_allocation.source_allocation)
        if allocation.session and frappe.db.exists("POS Payment Session", allocation.session):
            session = frappe.get_doc("POS Payment Session", allocation.session)
            if session.mobile_number:
                return _normalize_whatsapp_number(session.mobile_number), allocation, session
        if allocation.mobile_number:
            return _normalize_whatsapp_number(allocation.mobile_number), allocation, session

    invoice = None
    if refund_allocation.original_invoice and frappe.db.exists(
        "Sales Invoice", refund_allocation.original_invoice
    ):
        invoice = frappe.get_doc("Sales Invoice", refund_allocation.original_invoice)
        mobile = getattr(invoice, "contact_mobile", None) or getattr(invoice, "mobile_no", None)
        if mobile:
            return _normalize_whatsapp_number(mobile), allocation, session
        customer = getattr(invoice, "customer", None)
        if customer and frappe.db.exists("Customer", customer):
            mobile = frappe.db.get_value("Customer", customer, "mobile_no")
            if mobile:
                return _normalize_whatsapp_number(mobile), allocation, session

    return None, allocation, session


def build_refund_message(refund_allocation):
    settings = get_settings()
    refund_txn = None
    source_txn = None
    if refund_allocation.refund_gateway_transaction and frappe.db.exists(
        "Gateway Transaction", refund_allocation.refund_gateway_transaction
    ):
        refund_txn = frappe.get_doc(
            "Gateway Transaction", refund_allocation.refund_gateway_transaction
        )
    if refund_allocation.source_gateway_transaction and frappe.db.exists(
        "Gateway Transaction", refund_allocation.source_gateway_transaction
    ):
        source_txn = frappe.get_doc(
            "Gateway Transaction", refund_allocation.source_gateway_transaction
        )

    invoice = None
    if refund_allocation.original_invoice and frappe.db.exists(
        "Sales Invoice", refund_allocation.original_invoice
    ):
        invoice = frappe.get_doc("Sales Invoice", refund_allocation.original_invoice)

    provider_refund_id = (
        getattr(refund_allocation, "provider_refund_id", None)
        or getattr(refund_txn, "provider_refund_id", None)
        or ""
    )
    provider_reference = (
        getattr(refund_allocation, "provider_reference", None)
        or getattr(refund_txn, "provider_tracking_id", None)
        or getattr(refund_txn, "provider_payment_id", None)
        or ""
    )
    provider_auth_no = (
        getattr(refund_allocation, "provider_auth_no", None)
        or provider_authorization_reference(refund_txn)
        or provider_authorization_reference(source_txn)
        or ""
    )
    values = {
        "customer_name": getattr(invoice, "customer_name", None) or getattr(invoice, "customer", None) or "Customer",
        "amount": f"{float(refund_allocation.amount or 0):.3f}",
        "currency": refund_allocation.currency or "",
        "mode_of_payment": refund_allocation.actual_refund_mode_of_payment or refund_allocation.mode_of_payment or "",
        "actual_payment_method": refund_allocation.actual_payment_method or getattr(source_txn, "provider_payment_type", None) or "",
        "provider": refund_allocation.provider or getattr(refund_txn, "provider", None) or "",
        "refund_transaction": getattr(refund_txn, "name", None) or "",
        "original_transaction": refund_allocation.source_gateway_transaction or "",
        "provider_refund_id": provider_refund_id,
        "provider_reference": provider_reference,
        "provider_auth_no": provider_auth_no,
        "provider_transaction_id": getattr(refund_allocation, "source_provider_transaction_id", None) or getattr(source_txn, "provider_transaction_id", None) or "",
        "provider_payment_id": getattr(refund_allocation, "source_provider_payment_id", None) or getattr(source_txn, "provider_payment_id", None) or "",
        "refund_provider_transaction_id": getattr(refund_allocation, "refund_provider_transaction_id", None) or getattr(refund_txn, "provider_transaction_id", None) or "",
        "original_invoice": refund_allocation.original_invoice or "",
        "return_invoice": refund_allocation.return_invoice or "",
        "status": "Completed" if refund_allocation.status == "Completed" else "Pending",
    }

    template = (getattr(settings, "refund_message_template", None) or "").strip()
    if template:
        class SafeDict(dict):
            def __missing__(self, key):
                return ""
        return template.format_map(SafeDict(values))

    lines = [
        "Refund Confirmation",
        f"Amount: {values['currency']} {values['amount']}",
        f"Mode of Payment: {values['mode_of_payment']}",
    ]
    if values["actual_payment_method"]:
        lines.append(f"Payment Method: {values['actual_payment_method']}")
    if values["provider"]:
        lines.append(f"Provider: {values['provider']}")
    if values["refund_transaction"]:
        lines.append(f"Refund Transaction: {values['refund_transaction']}")
    if values["refund_provider_transaction_id"]:
        lines.append(f"Refund Provider Transaction: {values['refund_provider_transaction_id']}")
    if values["provider_transaction_id"]:
        lines.append(f"Provider Payment Transaction: {values['provider_transaction_id']}")
    if values["provider_payment_id"]:
        lines.append(f"Provider Payment ID: {values['provider_payment_id']}")
    if values["provider_refund_id"]:
        lines.append(f"Provider Refund ID: {values['provider_refund_id']}")
    if values["provider_reference"]:
        lines.append(f"Provider Reference / ARN: {values['provider_reference']}")
    if values["provider_auth_no"]:
        lines.append(f"Provider Auth No: {values['provider_auth_no']}")
    if values["original_invoice"]:
        lines.append(f"Original Invoice: {values['original_invoice']}")
    if values["return_invoice"]:
        lines.append(f"Return Invoice: {values['return_invoice']}")
    if values["status"] == "Completed":
        lines.append("Your refund has been processed successfully.")
    else:
        lines.append("Your refund request has been submitted and is being processed.")
    return "\n".join(lines)


def send_refund_message(*, refund_allocation, message=None):
    settings = get_settings()
    if not int(getattr(settings, "send_refund_whatsapp", 1) or 0):
        return {"skipped": True, "reason": "disabled"}

    to, allocation, session = refund_customer_mobile(refund_allocation)
    if not to:
        return {"skipped": True, "reason": "customer_mobile_missing"}

    message = message or build_refund_message(refund_allocation)
    method = (getattr(settings, "whatsapp_sender_method", None) or "").strip()
    if method:
        result = _call_custom_refund_sender(
            method,
            to=to,
            message=message,
            refund_allocation=refund_allocation,
            allocation=allocation,
            session=session,
        )
        return {
            "integration": "Custom",
            "method": method,
            "result": result,
            "to": to,
        }

    integration = getattr(settings, "whatsapp_integration", None) or "Frappe WhatsApp"
    if integration in ("Frappe WhatsApp", "Meta WhatsApp Cloud API"):
        result = _send_refund_with_frappe_whatsapp(
            to=to,
            message=message,
            refund_allocation=refund_allocation,
            settings=settings,
        )
        result["to"] = to
        return result

    frappe.throw(f"Unsupported WhatsApp integration: {integration}")

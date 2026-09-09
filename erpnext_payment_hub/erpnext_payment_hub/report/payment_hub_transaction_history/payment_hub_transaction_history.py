
from __future__ import annotations
import frappe
from frappe import _
from erpnext_payment_hub.pos.reporting import transaction_history


def execute(filters=None):
    f = frappe._dict(filters or {})
    rows = transaction_history(
        search=f.get("search"), from_date=f.get("from_date"), to_date=f.get("to_date"),
        pos_profile=f.get("pos_profile"), cashier=f.get("cashier"), provider=f.get("provider"),
        include_pending=True, limit=5000,
    )
    columns = [
        {"label": _("Date / Time"), "fieldname": "transaction_datetime", "fieldtype": "Datetime", "width": 150},
        {"label": _("Type"), "fieldname": "transaction_type", "fieldtype": "Data", "width": 105},
        {"label": _("Invoice"), "fieldname": "invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 165},
        {"label": _("Return Invoice"), "fieldname": "return_invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 165},
        {"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 145},
        {"label": _("Mobile"), "fieldname": "mobile_number", "fieldtype": "Data", "width": 115},
        {"label": _("POS Profile"), "fieldname": "pos_profile", "fieldtype": "Link", "options": "POS Profile", "width": 105},
        {"label": _("Cashier"), "fieldname": "cashier_user", "fieldtype": "Link", "options": "User", "width": 135},
        {"label": _("Channel"), "fieldname": "channel", "fieldtype": "Data", "width": 145},
        {"label": _("Mode of Payment"), "fieldname": "mode_of_payment", "fieldtype": "Data", "width": 145},
        {"label": _("Provider"), "fieldname": "provider", "fieldtype": "Data", "width": 115},
        {"label": _("Method"), "fieldname": "actual_payment_method", "fieldtype": "Data", "width": 85},
        {"label": _("Amount"), "fieldname": "signed_amount", "fieldtype": "Currency", "options": "currency", "precision": 3, "width": 105},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
        {"label": _("Gateway Transaction"), "fieldname": "gateway_transaction", "fieldtype": "Link", "options": "Gateway Transaction", "width": 155},
        {"label": _("Provider Reference"), "fieldname": "provider_reference", "fieldtype": "Data", "width": 185},
        {"label": _("Authorized By"), "fieldname": "authorized_by", "fieldtype": "Link", "options": "User", "width": 130},
        {"label": _("Override"), "fieldname": "override_text", "fieldtype": "Data", "width": 75},
        {"label": _("Remaining Refundable"), "fieldname": "remaining_refundable", "fieldtype": "Currency", "options": "currency", "precision": 3, "width": 135},
    ]
    data=[]
    for row in rows:
        x=dict(row)
        x["provider_reference"] = row.get("provider_refund_id") or row.get("provider_transaction_id") or row.get("provider_payment_id") or row.get("provider_tracking_id")
        x["override_text"] = _("Yes") if row.get("is_override") else _("No")
        data.append(x)
    return columns, data

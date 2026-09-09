from __future__ import annotations

import frappe
from frappe import _

from erpnext_payment_hub.pos.reporting import daily_report_data


def execute(filters=None):
    filters = frappe._dict(filters or {})
    report = daily_report_data(
        from_date=filters.get("from_date"),
        to_date=filters.get("to_date"),
        pos_profile=filters.get("pos_profile"),
        cashier=filters.get("cashier"),
        transaction_type=filters.get("transaction_type"),
        channel=filters.get("channel"),
        provider=filters.get("provider"),
        status=filters.get("status"),
        search=filters.get("search"),
    )
    columns = [
        {"label": _("Date / Time"), "fieldname": "transaction_datetime", "fieldtype": "Datetime", "width": 150},
        {"label": _("Type"), "fieldname": "transaction_type", "fieldtype": "Data", "width": 90},
        {"label": _("Invoice"), "fieldname": "invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 170},
        {"label": _("Return Invoice"), "fieldname": "return_invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 170},
        {"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 150},
        {"label": _("Mobile"), "fieldname": "mobile_number", "fieldtype": "Data", "width": 120},
        {"label": _("POS Profile"), "fieldname": "pos_profile", "fieldtype": "Link", "options": "POS Profile", "width": 110},
        {"label": _("Cashier"), "fieldname": "cashier_user", "fieldtype": "Link", "options": "User", "width": 130},
        {"label": _("Channel"), "fieldname": "channel", "fieldtype": "Data", "width": 150},
        {"label": _("Mode of Payment"), "fieldname": "mode_of_payment", "fieldtype": "Data", "width": 150},
        {"label": _("Provider"), "fieldname": "provider", "fieldtype": "Data", "width": 120},
        {"label": _("Method"), "fieldname": "actual_payment_method", "fieldtype": "Data", "width": 90},
        {"label": _("Amount"), "fieldname": "signed_amount", "fieldtype": "Currency", "options": "currency", "precision": 3, "width": 110},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
        {"label": _("Gateway Transaction"), "fieldname": "gateway_transaction", "fieldtype": "Link", "options": "Gateway Transaction", "width": 150},
        {"label": _("Provider Txn / Refund ID"), "fieldname": "provider_reference", "fieldtype": "Data", "width": 180},
        {"label": _("Authorized By"), "fieldname": "authorized_by", "fieldtype": "Link", "options": "User", "width": 130},
        {"label": _("Override"), "fieldname": "override_text", "fieldtype": "Data", "width": 90},
    ]
    data = []
    for row in report["rows"]:
        item = dict(row)
        item["provider_reference"] = row.get("provider_refund_id") or row.get("provider_transaction_id") or row.get("provider_payment_id")
        item["override_text"] = _("Yes") if row.get("is_override") else _("No")
        data.append(item)

    currency = report.get("currency") or "KWD"
    report_summary = [
        {"label": _("Payments"), "value": report["payment_total"], "indicator": "Green", "datatype": "Currency", "currency": currency},
        {"label": _("Refunds"), "value": report["refund_total"], "indicator": "Red", "datatype": "Currency", "currency": currency},
        {"label": _("Net Collection"), "value": report["net_total"], "indicator": "Blue", "datatype": "Currency", "currency": currency},
    ]
    return columns, data, None, None, report_summary

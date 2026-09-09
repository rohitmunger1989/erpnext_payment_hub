
from __future__ import annotations
import frappe
from frappe import _
from erpnext_payment_hub.pos.reporting import daily_report_data


def execute(filters=None):
    f=frappe._dict(filters or {})
    report=daily_report_data(from_date=f.get("from_date"),to_date=f.get("to_date"),pos_profile=f.get("pos_profile"),cashier=f.get("cashier"),limit=5000)
    columns=[
        {"label":_("Provider"),"fieldname":"provider","fieldtype":"Data","width":180},
        {"label":_("Payments"),"fieldname":"payments","fieldtype":"Currency","options":"currency","precision":3,"width":130},
        {"label":_("Refunds"),"fieldname":"refunds","fieldtype":"Currency","options":"currency","precision":3,"width":130},
        {"label":_("Net"),"fieldname":"net","fieldtype":"Currency","options":"currency","precision":3,"width":130},
    ]
    currency=report.get("currency") or "KWD"
    data=[{"provider":r.get("name"),"payments":r.get("payments"),"refunds":r.get("refunds"),"net":r.get("net"),"currency":currency} for r in report.get("provider_summary") or []]
    summary=[
        {"label":_("Payments"),"value":report.get("payment_total"),"indicator":"Green","datatype":"Currency","currency":currency},
        {"label":_("Refunds"),"value":report.get("refund_total"),"indicator":"Red","datatype":"Currency","currency":currency},
        {"label":_("Net"),"value":report.get("net_total"),"indicator":"Blue","datatype":"Currency","currency":currency},
    ]
    return columns,data,None,None,summary

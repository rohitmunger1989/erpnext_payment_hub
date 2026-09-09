
from __future__ import annotations
import frappe
from frappe import _
from erpnext_payment_hub.pos.reporting import transaction_history


def execute(filters=None):
    f=frappe._dict(filters or {})
    rows=transaction_history(search=f.get("search"), from_date=f.get("from_date"), to_date=f.get("to_date"), pos_profile=f.get("pos_profile"), cashier=f.get("cashier"), provider=f.get("provider"), transaction_type="Refund", include_pending=False, limit=5000)
    columns=[
        {"label":_("Date / Time"),"fieldname":"transaction_datetime","fieldtype":"Datetime","width":150},
        {"label":_("Return Invoice"),"fieldname":"return_invoice","fieldtype":"Link","options":"Sales Invoice","width":170},
        {"label":_("Original Invoice"),"fieldname":"invoice","fieldtype":"Link","options":"Sales Invoice","width":170},
        {"label":_("Customer"),"fieldname":"customer_name","fieldtype":"Data","width":145},
        {"label":_("Mobile"),"fieldname":"mobile_number","fieldtype":"Data","width":115},
        {"label":_("POS Profile"),"fieldname":"pos_profile","fieldtype":"Link","options":"POS Profile","width":110},
        {"label":_("Cashier"),"fieldname":"cashier_user","fieldtype":"Link","options":"User","width":135},
        {"label":_("Original Payment"),"fieldname":"original_mode_of_payment","fieldtype":"Data","width":145},
        {"label":_("Actual Refund"),"fieldname":"mode_of_payment","fieldtype":"Data","width":145},
        {"label":_("Provider"),"fieldname":"original_provider","fieldtype":"Data","width":115},
        {"label":_("Method"),"fieldname":"actual_payment_method","fieldtype":"Data","width":85},
        {"label":_("Refund Amount"),"fieldname":"refund_amount","fieldtype":"Currency","options":"currency","precision":3,"width":115},
        {"label":_("Status"),"fieldname":"status","fieldtype":"Data","width":110},
        {"label":_("Refund Gateway"),"fieldname":"gateway_transaction","fieldtype":"Link","options":"Gateway Transaction","width":155},
        {"label":_("Refund ID"),"fieldname":"provider_refund_id","fieldtype":"Data","width":190},
        {"label":_("Authorized By"),"fieldname":"authorized_by","fieldtype":"Link","options":"User","width":135},
        {"label":_("Override"),"fieldname":"override_text","fieldtype":"Data","width":75},
        {"label":_("Override Reason"),"fieldname":"override_reason","fieldtype":"Data","width":190},
    ]
    data=[]
    for row in rows:
        x=dict(row); x["refund_amount"]=-abs(row.get("amount") or 0); x["override_text"] = _("Yes") if row.get("is_override") else _("No"); data.append(x)
    return columns,data


from __future__ import annotations
import frappe
from frappe import _
from erpnext_payment_hub.pos.reporting import transaction_history

PENDING={"waiting","pending","payment pending","initiated","processing","reserved"}
FAILED={"failed","expired","cancelled","canceled","manual review"}


def execute(filters=None):
    f=frappe._dict(filters or {})
    rows=transaction_history(search=f.get("search"),from_date=f.get("from_date"),to_date=f.get("to_date"),pos_profile=f.get("pos_profile"),include_pending=True,limit=5000)
    group=(f.get("status_group") or "All").lower()
    def keep(r):
        s=str(r.get("status") or r.get("session_status") or "").strip().lower()
        if group=="pending": return s in PENDING or r.get("transaction_type")=="Pending Payment" and s not in FAILED
        if group=="failed": return s in FAILED
        return s in PENDING or s in FAILED or r.get("transaction_type")=="Pending Payment"
    rows=[r for r in rows if keep(r)]
    columns=[
        {"label":_("Date / Time"),"fieldname":"transaction_datetime","fieldtype":"Datetime","width":150},
        {"label":_("Type"),"fieldname":"transaction_type","fieldtype":"Data","width":110},
        {"label":_("Session / Invoice"),"fieldname":"reference","fieldtype":"Data","width":180},
        {"label":_("Customer"),"fieldname":"customer_name","fieldtype":"Data","width":145},
        {"label":_("Mobile"),"fieldname":"mobile_number","fieldtype":"Data","width":115},
        {"label":_("POS Profile"),"fieldname":"pos_profile","fieldtype":"Link","options":"POS Profile","width":110},
        {"label":_("Cashier"),"fieldname":"cashier_user","fieldtype":"Link","options":"User","width":135},
        {"label":_("Provider"),"fieldname":"provider","fieldtype":"Data","width":115},
        {"label":_("Method"),"fieldname":"actual_payment_method","fieldtype":"Data","width":90},
        {"label":_("Amount"),"fieldname":"amount","fieldtype":"Currency","options":"currency","precision":3,"width":110},
        {"label":_("Status"),"fieldname":"status","fieldtype":"Data","width":115},
        {"label":_("Gateway Transaction"),"fieldname":"gateway_transaction","fieldtype":"Link","options":"Gateway Transaction","width":160},
        {"label":_("Provider Reference"),"fieldname":"provider_reference","fieldtype":"Data","width":190},
    ]
    data=[]
    for r in rows:
        x=dict(r); x["reference"]=r.get("session") or r.get("return_invoice") or r.get("invoice"); x["provider_reference"]=r.get("provider_transaction_id") or r.get("provider_payment_id") or r.get("provider_tracking_id") or r.get("provider_refund_id"); data.append(x)
    return columns,data

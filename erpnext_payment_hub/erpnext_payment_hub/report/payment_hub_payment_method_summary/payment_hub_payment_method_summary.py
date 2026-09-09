
from __future__ import annotations
from collections import defaultdict
import frappe
from frappe import _
from frappe.utils import flt
from erpnext_payment_hub.pos.reporting import transaction_history


def execute(filters=None):
    f=frappe._dict(filters or {})
    rows=transaction_history(from_date=f.get("from_date"),to_date=f.get("to_date"),pos_profile=f.get("pos_profile"),provider=f.get("provider"),include_pending=False,limit=5000)
    agg=defaultdict(lambda:{"payments":0.0,"refunds":0.0,"currency":"KWD"})
    for r in rows:
        key=(r.get("channel") or "Other",r.get("mode_of_payment") or "",r.get("provider") or "Non-Gateway",r.get("actual_payment_method") or "")
        a=agg[key]; a["currency"]=r.get("currency") or a["currency"]
        if r.get("transaction_type")=="Refund": a["refunds"]+=flt(r.get("amount"),3)
        else: a["payments"]+=flt(r.get("amount"),3)
    data=[]
    for key,a in sorted(agg.items()):
        channel,mode,provider,method=key
        data.append({"channel":channel,"mode_of_payment":mode,"provider":provider,"actual_payment_method":method,"payments":flt(a["payments"],3),"refunds":flt(a["refunds"],3),"net":flt(a["payments"]-a["refunds"],3),"currency":a["currency"]})
    columns=[
        {"label":_("Channel"),"fieldname":"channel","fieldtype":"Data","width":170},
        {"label":_("Mode of Payment"),"fieldname":"mode_of_payment","fieldtype":"Data","width":160},
        {"label":_("Provider"),"fieldname":"provider","fieldtype":"Data","width":130},
        {"label":_("Method"),"fieldname":"actual_payment_method","fieldtype":"Data","width":100},
        {"label":_("Payments"),"fieldname":"payments","fieldtype":"Currency","options":"currency","precision":3,"width":120},
        {"label":_("Refunds"),"fieldname":"refunds","fieldtype":"Currency","options":"currency","precision":3,"width":120},
        {"label":_("Net"),"fieldname":"net","fieldtype":"Currency","options":"currency","precision":3,"width":120},
    ]
    return columns,data

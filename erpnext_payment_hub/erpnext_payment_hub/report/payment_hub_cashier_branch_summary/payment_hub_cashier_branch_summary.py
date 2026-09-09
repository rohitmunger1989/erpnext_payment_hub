
from __future__ import annotations
from collections import defaultdict
import frappe
from frappe import _
from frappe.utils import flt
from erpnext_payment_hub.pos.reporting import transaction_history


def execute(filters=None):
    f=frappe._dict(filters or {})
    rows=transaction_history(from_date=f.get("from_date"),to_date=f.get("to_date"),pos_profile=f.get("pos_profile"),cashier=f.get("cashier"),include_pending=False,limit=5000)
    agg=defaultdict(lambda:{"payments":0.0,"refunds":0.0,"cash":0.0,"electronic":0.0,"physical":0.0,"currency":"KWD"})
    for r in rows:
        key=(r.get("pos_profile") or "Unassigned",r.get("cashier_user") or "Unknown")
        a=agg[key]; a["currency"]=r.get("currency") or a["currency"]
        amount=flt(r.get("amount"),3)
        if r.get("transaction_type")=="Refund": a["refunds"]+=amount
        else: a["payments"]+=amount
        ch=r.get("channel")
        signed=-amount if r.get("transaction_type")=="Refund" else amount
        if ch=="Cash": a["cash"]+=signed
        elif ch=="Electronic Payment": a["electronic"]+=signed
        elif ch=="Physical Payment Terminal": a["physical"]+=signed
    data=[]
    for (pos,cashier),a in sorted(agg.items()):
        data.append({"pos_profile":pos,"cashier":cashier,"payments":flt(a["payments"],3),"refunds":flt(a["refunds"],3),"net":flt(a["payments"]-a["refunds"],3),"cash":flt(a["cash"],3),"electronic":flt(a["electronic"],3),"physical":flt(a["physical"],3),"currency":a["currency"]})
    columns=[
        {"label":_("POS Profile / Branch"),"fieldname":"pos_profile","fieldtype":"Data","width":170},
        {"label":_("Cashier"),"fieldname":"cashier","fieldtype":"Link","options":"User","width":150},
        {"label":_("Payments"),"fieldname":"payments","fieldtype":"Currency","options":"currency","precision":3,"width":115},
        {"label":_("Refunds"),"fieldname":"refunds","fieldtype":"Currency","options":"currency","precision":3,"width":115},
        {"label":_("Net Collection"),"fieldname":"net","fieldtype":"Currency","options":"currency","precision":3,"width":125},
        {"label":_("Net Cash"),"fieldname":"cash","fieldtype":"Currency","options":"currency","precision":3,"width":115},
        {"label":_("Net Electronic"),"fieldname":"electronic","fieldtype":"Currency","options":"currency","precision":3,"width":125},
        {"label":_("Net Physical Terminal"),"fieldname":"physical","fieldtype":"Currency","options":"currency","precision":3,"width":145},
    ]
    return columns,data

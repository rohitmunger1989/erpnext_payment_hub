from __future__ import annotations

import frappe
from frappe.utils import cint

CHANNEL_CASH = "Cash"
CHANNEL_MANUAL = "Manual / Non-Cash"
CHANNEL_ELECTRONIC = "Electronic Payment"
CHANNEL_PHYSICAL = "Physical Payment Terminal"
SUPPORTED_CHANNELS = (CHANNEL_CASH, CHANNEL_MANUAL, CHANNEL_ELECTRONIC, CHANNEL_PHYSICAL)


def normalize_mode(value):
    return str(value or "").strip().lower()


def _context(session=None, *, company=None, pos_profile=None):
    if session is not None:
        company = company or getattr(session, "company", None)
        pos_profile = pos_profile or getattr(session, "pos_profile", None)
    return frappe._dict(company=company, pos_profile=pos_profile)


def _matches_scope(row, ctx):
    if row.company and row.company != ctx.company:
        return False
    if row.pos_profile and row.pos_profile != ctx.pos_profile:
        return False
    return True


def _specificity(row):
    return (100 if row.pos_profile else 0) + (10 if row.company else 0)


def get_mode_mapping(mode_of_payment, *, session=None, company=None, pos_profile=None, channel=None):
    """Return the best enabled mapping for an ERPNext Mode of Payment.

    Exact POS Profile mappings win over company mappings, which win over global
    mappings. Lower ``priority`` wins between equally-specific rows.
    """
    wanted = normalize_mode(mode_of_payment)
    if not wanted:
        return None
    settings = frappe.get_single("Payment Hub Settings")
    ctx = _context(session, company=company, pos_profile=pos_profile)
    rows = []
    for row in getattr(settings, "payment_method_mappings", []) or []:
        if not cint(getattr(row, "enabled", 1)):
            continue
        if normalize_mode(row.mode_of_payment) != wanted:
            continue
        if channel and row.channel != channel:
            continue
        if not _matches_scope(row, ctx):
            continue
        rows.append(row)
    if not rows:
        return None
    rows.sort(key=lambda r: (-_specificity(r), int(getattr(r, "priority", 10) or 10), int(getattr(r, "idx", 0) or 0)))
    return rows[0]


def provider_account_for_mapping(mapping):
    if not mapping:
        return None
    if mapping.provider_account:
        return mapping.provider_account
    if mapping.payment_terminal and frappe.db.exists("Payment Terminal", mapping.payment_terminal):
        return frappe.db.get_value("Payment Terminal", mapping.payment_terminal, "provider_account")
    return None


def get_applicable_mappings(*, company=None, pos_profile=None):
    settings = frappe.get_single("Payment Hub Settings")
    ctx = _context(company=company, pos_profile=pos_profile)
    mode_names = []
    seen = set()
    for row in getattr(settings, "payment_method_mappings", []) or []:
        if not cint(getattr(row, "enabled", 1)) or not _matches_scope(row, ctx):
            continue
        key = normalize_mode(row.mode_of_payment)
        if key and key not in seen:
            seen.add(key)
            mode_names.append(row.mode_of_payment)
    result=[]
    for mode in mode_names:
        row=get_mode_mapping(mode, company=company, pos_profile=pos_profile)
        if row:
            result.append(row)
    result.sort(key=lambda r: (int(getattr(r,"priority",10) or 10), int(getattr(r,"idx",0) or 0), normalize_mode(r.mode_of_payment)))
    return result


def serialize_mapping(row):
    account_name = provider_account_for_mapping(row)
    provider = frappe.db.get_value("Payment Provider Account", account_name, "provider") if account_name else None
    terminal_id = None
    terminal_enabled = None
    if row.payment_terminal and frappe.db.exists("Payment Terminal", row.payment_terminal):
        terminal_id, terminal_enabled = frappe.db.get_value(
            "Payment Terminal", row.payment_terminal, ["terminal_id", "enabled"]
        )
    return {
        "mode_of_payment": row.mode_of_payment,
        "channel": row.channel,
        "provider_account": account_name,
        "provider": provider,
        "payment_terminal": row.payment_terminal,
        "terminal_id": terminal_id,
        "terminal_enabled": bool(terminal_enabled) if terminal_enabled is not None else None,
        "payment_method": row.provider_payment_method,
        "company": row.company,
        "pos_profile": row.pos_profile,
        "priority": int(row.priority or 10),
    }


def assert_mapping_channel(mode_of_payment, channel, *, session=None, company=None, pos_profile=None):
    row = get_mode_mapping(
        mode_of_payment, session=session, company=company, pos_profile=pos_profile
    )
    if row and row.channel != channel:
        frappe.throw(
            f"Mode of Payment {mode_of_payment} is mapped to {row.channel}, not {channel}."
        )
    return row

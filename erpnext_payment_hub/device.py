from __future__ import annotations

import frappe

from erpnext_payment_hub.gateway import resolve_pos_station, touch_pos_station


@frappe.whitelist()
def identify_station(computer_name=None, pairing_code=None, pos_profile=None, branch=None):
    station = resolve_pos_station(
        computer_name=computer_name,
        pairing_code=pairing_code,
        pos_profile=pos_profile,
        branch=branch,
    )

    if not station:
        return {"found": False}

    touch_pos_station(station)

    return {
        "found": True,
        "station": station.name,
        "computer_name": station.computer_name,
        "branch": station.branch,
        "pos_profile": station.pos_profile,
        "provider_account": station.provider_account,
        "payment_terminal": station.payment_terminal,
        "terminal_id": station.terminal_id,
    }


@frappe.whitelist()
def regenerate_pairing_code(station_name):
    frappe.only_for("System Manager")
    station = frappe.get_doc("POS Station", station_name)

    import secrets
    station.pairing_code = secrets.token_urlsafe(18)
    station.save()

    return {
        "station": station.name,
        "pairing_code": station.get_password("pairing_code"),
    }

from __future__ import annotations

import frappe


PAYMENT_HUB_DESK_ROLES = {
    "System Manager",
    "Accounts Manager",
    "Accounts User",
    "Payment Hub Auditor",
    "Payment Hub Refund Approver",
    "Payment Hub Refund Override",
}


def check_app_permission():
    """Return whether the current user may enter the Payment Hub Desk app."""
    if frappe.session.user == "Administrator":
        return True
    if frappe.session.user == "Guest":
        return False
    return bool(PAYMENT_HUB_DESK_ROLES.intersection(frappe.get_roles()))

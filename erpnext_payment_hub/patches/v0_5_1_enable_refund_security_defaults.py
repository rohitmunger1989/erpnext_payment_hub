import frappe
from frappe.utils import cint


def execute():
    """Seed secure v0.5 refund defaults for sites upgraded from older versions.

    Check fields added to an existing Single DocType can materialize as 0 during
    schema migration even when the JSON default is 1. This one-time patch makes
    the intended secure defaults explicit. Administrators can change the
    settings afterwards from Payment Hub Settings.
    """
    settings = frappe.get_single("Payment Hub Settings")

    settings.require_authorization_electronic_refund = 1
    settings.require_authorization_physical_refund = 1
    settings.allow_refund_method_override = 1

    # Cash refunds remain policy-configurable and default to no manager approval.
    if getattr(settings, "require_authorization_cash_refund", None) is None:
        settings.require_authorization_cash_refund = 0

    if cint(getattr(settings, "refund_authorization_minutes", 0)) <= 0:
        settings.refund_authorization_minutes = 5

    if not getattr(settings, "cash_mode_of_payment", None):
        settings.cash_mode_of_payment = "Cash"

    settings.save(ignore_permissions=True)

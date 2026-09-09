import frappe


PROVIDERS = (
    ("Tap Payments", "Tap Payments"),
    ("MyFatoorah", "MyFatoorah"),
    ("UPayments", "UPayments"),
)

REFUND_ROLES = (
    "Payment Hub Refund Approver",
    "Payment Hub Refund Override",
    "Payment Hub Auditor",
)


def ensure_refund_roles():
    for role_name in REFUND_ROLES:
        if not frappe.db.exists("Role", role_name):
            role = frappe.new_doc("Role")
            role.role_name = role_name
            role.desk_access = 1
            role.insert(ignore_permissions=True)


def _set_default(settings, fieldname, value):
    if not hasattr(settings, fieldname):
        return
    current = getattr(settings, fieldname, None)
    if current is None or current == "":
        setattr(settings, fieldname, value)


def ensure_settings_defaults():
    settings = frappe.get_single("Payment Hub Settings")
    defaults = {
        "force_original_payment_method_refund": 1,
        "allow_partial_refund": 1,
        "verify_by_status_api": 1,
        "auto_reconcile": 1,
        "cash_mode_of_payment": "Cash",
        "physical_mode_of_payment": "Physical Payment Terminal",
        "electronic_mode_of_payment": "Electronic Payment",
        "whatsapp_integration": "Frappe WhatsApp",
        "async_electronic_payment": 1,
        "after_electronic_capture": "Mark Paid Only",
        "payment_link_expiry_minutes": 30,
        "auto_expire_pending_sales": 1,
        "default_print_format": "Standard",
        "require_authorization_cash_refund": 0,
        "require_authorization_electronic_refund": 1,
        "require_authorization_physical_refund": 1,
        "allow_refund_method_override": 1,
        "refund_authorization_minutes": 5,
    }
    for fieldname, value in defaults.items():
        _set_default(settings, fieldname, value)
    settings.save(ignore_permissions=True)


def ensure_provider_accounts():
    for account_name, provider in PROVIDERS:
        if not frappe.db.exists("Payment Provider Account", account_name):
            doc = frappe.new_doc("Payment Provider Account")
            doc.account_name = account_name
            doc.provider = provider
            doc.status = "Disabled"
            doc.test_mode = 1
            doc.insert(ignore_permissions=True)


def ensure_desktop_icon():
    """Create missing Frappe v16 app-screen icons after upgrades.

    Frappe normally seeds Desktop Icon rows during a fresh app install. Existing
    sites upgraded from Payment Hub releases that predate ``add_to_apps_screen``
    can therefore have the workspace but no app icon. On Frappe versions that
    provide the v16 Desktop Icon helpers, re-scan installed apps idempotently.
    Frappe v15 does not provide this API, so simply skip it there.
    """
    try:
        from frappe.desk.doctype.desktop_icon.desktop_icon import (
            create_desktop_icons_from_installed_apps,
        )
    except (ImportError, ModuleNotFoundError):
        return

    create_desktop_icons_from_installed_apps()


def after_install():
    ensure_refund_roles()
    ensure_settings_defaults()
    ensure_provider_accounts()
    ensure_desktop_icon()


def after_migrate():
    """Idempotent post-migration setup for existing installations."""
    ensure_refund_roles()
    ensure_settings_defaults()
    ensure_desktop_icon()

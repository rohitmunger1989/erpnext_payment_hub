import frappe


PROVIDERS = (
    ("Tap Payments", "Tap Payments"),
    ("MyFatoorah", "MyFatoorah"),
    ("UPayments", "UPayments"),
)


def after_install():
    settings = frappe.get_single("Payment Hub Settings")
    settings.force_original_payment_method_refund = 1
    settings.allow_partial_refund = 1
    settings.verify_by_status_api = 1
    settings.auto_reconcile = 1
    settings.save(ignore_permissions=True)

    for account_name, provider in PROVIDERS:
        if not frappe.db.exists("Payment Provider Account", account_name):
            doc = frappe.new_doc("Payment Provider Account")
            doc.account_name = account_name
            doc.provider = provider
            doc.status = "Disabled"
            doc.test_mode = 1
            doc.insert(ignore_permissions=True)

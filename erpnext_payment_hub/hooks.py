app_name = "erpnext_payment_hub"
app_title = "ERPNext Payment Hub"
app_publisher = "ERPNext Payment Hub Contributors"
app_description = "Provider-agnostic payment gateway router for ERPNext"
app_email = ""
app_license = "MIT"

app_logo_url = "/assets/erpnext_payment_hub/images/payment-hub-logo.svg"
app_home = "/desk/payment-hub"

add_to_apps_screen = [
    {
        "name": app_name,
        "logo": app_logo_url,
        "title": "Payment Hub",
        "route": app_home,
        "has_permission": "erpnext_payment_hub.permissions.check_app_permission",
        "sequence_id": 25,
    }
]

required_apps = ["erpnext"]

after_install = "erpnext_payment_hub.install.after_install"
before_migrate = "erpnext_payment_hub.install.ensure_refund_roles"
after_migrate = "erpnext_payment_hub.install.after_migrate"


doc_events = {
    "Sales Invoice": {
        "before_submit": "erpnext_payment_hub.pos.refund.validate_sales_invoice_return_security",
    }
}


# Webhooks are primary; this is a low-rate reconciliation fallback for pending POS payments.
scheduler_events = {
    "cron": {
        "*/5 * * * *": [
            "erpnext_payment_hub.pos.service.reconcile_pending_pos_payments",
            "erpnext_payment_hub.pos.service.expire_stale_pos_payments"
        ]
    }
}

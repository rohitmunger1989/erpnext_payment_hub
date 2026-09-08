app_name = "erpnext_payment_hub"
app_title = "ERPNext Payment Hub"
app_publisher = "ERPNext Payment Hub Contributors"
app_description = "Provider-agnostic payment gateway router for ERPNext"
app_email = ""
app_license = "MIT"

required_apps = ["erpnext"]

after_install = "erpnext_payment_hub.install.after_install"


# Webhooks are primary; this is a low-rate reconciliation fallback for pending POS payments.
scheduler_events = {
    "cron": {
        "*/5 * * * *": [
            "erpnext_payment_hub.pos.service.reconcile_pending_pos_payments"
        ]
    }
}

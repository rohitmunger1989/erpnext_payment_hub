app_name = "erpnext_payment_hub"
app_title = "ERPNext Payment Hub"
app_publisher = "ERPNext Payment Hub Contributors"
app_description = "Provider-agnostic payment gateway router for ERPNext"
app_email = ""
app_license = "MIT"

required_apps = ["erpnext"]

after_install = "erpnext_payment_hub.install.after_install"

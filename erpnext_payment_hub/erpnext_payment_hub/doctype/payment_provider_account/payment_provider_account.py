import frappe
from frappe.model.document import Document


class PaymentProviderAccount(Document):
    def validate(self):
        if self.status in ("Active", "Test") and not (
            self.get_password("secret_key", raise_exception=False)
            or self.get_password("api_key", raise_exception=False)
        ):
            frappe.throw("Enter an API Key or Secret Key before activating this provider.")

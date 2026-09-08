import secrets

import frappe
from frappe.model.document import Document


class POSStation(Document):
    def validate(self):
        if self.computer_name:
            self.computer_name = self.computer_name.strip().upper()

        if self.payment_terminal:
            terminal = frappe.get_doc("Payment Terminal", self.payment_terminal)
            if terminal.provider_account != self.provider_account:
                frappe.throw(
                    "Payment Terminal must belong to the same Provider Account as the POS Station."
                )
            self.terminal_id = terminal.terminal_id
            self.device_id = terminal.device_id

        if not self.pairing_code:
            self.pairing_code = secrets.token_urlsafe(18)

        if self.computer_name:
            duplicate = frappe.db.exists(
                "POS Station",
                {
                    "name": ["!=", self.name],
                    "computer_name": self.computer_name,
                },
            )
            if duplicate:
                frappe.throw(
                    f"Computer Name {self.computer_name} is already assigned to {duplicate}."
                )

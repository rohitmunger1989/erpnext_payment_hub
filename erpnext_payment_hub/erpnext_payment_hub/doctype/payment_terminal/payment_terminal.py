import frappe
from frappe.model.document import Document


class PaymentTerminal(Document):
    def validate(self):
        if self.provider_account:
            self.provider = frappe.db.get_value(
                "Payment Provider Account", self.provider_account, "provider"
            )

        if not self.pos_profile and not self.branch:
            frappe.throw("Set at least a POS Profile or Branch for this terminal.")

        duplicate = frappe.db.exists(
            "Payment Terminal",
            {
                "name": ["!=", self.name],
                "provider_account": self.provider_account,
                "terminal_id": self.terminal_id,
            },
        )
        if duplicate:
            frappe.throw(
                f"Terminal ID {self.terminal_id} is already configured for "
                f"{self.provider_account}."
            )

        if self.is_default_for_branch and self.branch:
            other_default = frappe.db.exists(
                "Payment Terminal",
                {
                    "name": ["!=", self.name],
                    "provider_account": self.provider_account,
                    "branch": self.branch,
                    "is_default_for_branch": 1,
                    "enabled": 1,
                },
            )
            if other_default:
                frappe.throw(
                    f"{other_default} is already the default terminal for branch {self.branch}."
                )

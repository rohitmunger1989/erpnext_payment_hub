import frappe
from frappe.model.document import Document
from frappe.utils import cint


class PaymentHubSettings(Document):
    def validate(self):
        self._validate_payment_method_mappings()

    def _validate_payment_method_mappings(self):
        seen = set()
        for row in self.payment_method_mappings or []:
            if not cint(row.enabled):
                continue
            key = (
                (row.mode_of_payment or "").strip().lower(),
                row.company or "",
                row.pos_profile or "",
            )
            if key in seen:
                frappe.throw(
                    f"Duplicate Payment Method Mapping for {row.mode_of_payment} "
                    f"with the same Company/POS Profile scope (row {row.idx})."
                )
            seen.add(key)

            if row.channel == "Electronic Payment" and not row.provider_account:
                frappe.throw(
                    f"Provider Account is required for Electronic Payment mapping {row.mode_of_payment}."
                )

            if row.channel == "Physical Payment Terminal" and not (row.provider_account or row.payment_terminal):
                frappe.throw(
                    f"Provider Account or Payment Terminal is required for Physical Payment Terminal mapping {row.mode_of_payment}."
                )

            if row.payment_terminal:
                terminal = frappe.get_doc("Payment Terminal", row.payment_terminal)
                if row.channel != "Physical Payment Terminal":
                    frappe.throw(
                        f"Payment Terminal can only be set on Physical Payment Terminal mappings (row {row.idx})."
                    )
                if row.provider_account and terminal.provider_account != row.provider_account:
                    frappe.throw(
                        f"Payment Terminal {row.payment_terminal} belongs to {terminal.provider_account}, "
                        f"not {row.provider_account}."
                    )

            if row.channel in ("Cash", "Manual / Non-Cash") and (row.provider_account or row.payment_terminal):
                frappe.throw(
                    f"{row.channel} mapping {row.mode_of_payment} must not have a Provider Account or Payment Terminal."
                )

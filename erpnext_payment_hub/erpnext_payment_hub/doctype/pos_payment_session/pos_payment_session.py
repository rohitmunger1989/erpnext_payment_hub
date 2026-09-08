from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class POSPaymentSession(Document):
    def validate(self):
        self.grand_total = flt(self.grand_total, 3)
        if self.grand_total <= 0:
            frappe.throw("POS Payment Session Grand Total must be greater than zero.")
        if not self.currency:
            self.currency = "KWD"

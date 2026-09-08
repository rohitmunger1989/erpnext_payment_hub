from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class POSPaymentAllocation(Document):
    def validate(self):
        self.amount = flt(self.amount, 3)
        if self.amount <= 0:
            frappe.throw("POS Payment Allocation amount must be greater than zero.")

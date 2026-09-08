import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_payment_hub.gateway import get_provider


class TestGatewayFactory(FrappeTestCase):
    def test_seeded_provider_accounts_exist(self):
        for name in ("Tap Payments", "MyFatoorah", "UPayments"):
            self.assertTrue(frappe.db.exists("Payment Provider Account", name))

    def test_provider_factory(self):
        account = frappe.get_doc("Payment Provider Account", "Tap Payments")
        provider = get_provider(account)
        self.assertEqual(provider.provider_name, "Tap Payments")

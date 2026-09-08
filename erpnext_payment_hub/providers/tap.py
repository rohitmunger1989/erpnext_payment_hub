from __future__ import annotations

import frappe

from erpnext_payment_hub.providers.base import BaseProvider, ProviderError


class TapProvider(BaseProvider):
    provider_name = "Tap Payments"
    default_base_url = "https://api.tap.company/v2"

    def base_url(self):
        return (self.account.base_url_override or self.default_base_url).rstrip("/")

    def _source(self, payment_method):
        method = (payment_method or "ALL").upper().replace(" ", "_")
        return {
            "KNET": "src_kw.knet",
            "CARD": "src_card",
            "CREDIT_CARD": "src_card",
            "DEBIT_CARD": "src_card",
            "ALL": "src_all",
        }.get(method, "src_all")

    def create_payment(
        self,
        *,
        amount,
        currency,
        reference_doctype,
        reference_name,
        payment_method,
        customer,
        return_url,
        cancel_url=None,
        webhook_url=None,
        terminal=None,
        pos_context=None,
    ):
        if not return_url:
            raise ProviderError("Tap requires a redirect URL.")

        if float(amount) < 0.100:
            raise ProviderError(
                "Tap minimum charge amount is 0.100 in the transaction currency. "
                "Use at least KD 0.100 for a KWD test."
            )

        payload = {
            "amount": float(amount),
            "currency": currency,
            "customer_initiated": True,
            "threeDSecure": True,
            "save_card": False,
            "description": f"{reference_doctype} {reference_name}",
            "reference": {
                "transaction": reference_name,
                "order": reference_name,
            },
            "metadata": {
                "erpnext_doctype": reference_doctype,
                "erpnext_docname": reference_name,
            },
            "customer": {
                "first_name": customer.get("name") or "Customer",
                "email": customer.get("email") or "",
                "phone": {
                    "country_code": customer.get("phone_country_code") or "965",
                    "number": customer.get("phone") or "00000000",
                },
            },
            "source": {"id": self._source(payment_method)},
            "redirect": {"url": return_url},
        }

        if self.account.merchant_id:
            payload["merchant"] = {"id": self.account.merchant_id}
        if webhook_url:
            payload["post"] = {"url": webhook_url}

        result = self.request("POST", f"{self.base_url()}/charges/", json_data=payload)

        source = result.get("source") or {}
        return {
            "provider_transaction_id": result.get("id"),
            "provider_order_id": (result.get("reference") or {}).get("order") or reference_name,
            "provider_payment_id": (result.get("reference") or {}).get("payment"),
            "provider_tracking_id": (result.get("reference") or {}).get("gateway"),
            "payment_type": source.get("payment_method") or source.get("payment_type"),
            "status": result.get("status") or "INITIATED",
            "payment_url": (result.get("transaction") or {}).get("url"),
            "raw": result,
        }

    def get_payment_status(self, transaction):
        if not transaction.provider_transaction_id:
            raise ProviderError("Tap charge ID is missing.")
        result = self.request(
            "GET",
            f"{self.base_url()}/charges/{transaction.provider_transaction_id}",
        )
        source = result.get("source") or {}
        return {
            "status": result.get("status"),
            "provider_transaction_id": result.get("id"),
            "provider_order_id": (result.get("reference") or {}).get("order"),
            "provider_payment_id": (result.get("reference") or {}).get("payment"),
            "provider_tracking_id": (result.get("reference") or {}).get("gateway"),
            "payment_type": source.get("payment_method") or source.get("payment_type"),
            "raw": result,
        }

    def refund(self, transaction, amount, reason=None):
        if not transaction.provider_transaction_id:
            raise ProviderError("Original Tap charge ID is missing.")

        webhook_url = (
            f"{frappe.utils.get_url()}/api/method/"
            "erpnext_payment_hub.webhook.tap_refund"
            f"?provider_account={self.account.name}"
        )

        allowed_reasons = {"requested_by_customer", "duplicate", "fraudulent"}
        tap_reason = reason if reason in allowed_reasons else "requested_by_customer"

        payload = {
            "charge_id": transaction.provider_transaction_id,
            "amount": float(amount),
            "currency": transaction.currency or "KWD",
            "reason": tap_reason,
            "reference": {"merchant": transaction.reference_name or transaction.name},
            "metadata": {
                "erpnext_transaction": transaction.name,
                "refund_note": reason or "",
            },
            "post": {"url": webhook_url},
        }

        result = self.request("POST", f"{self.base_url()}/refunds/", json_data=payload)
        return {
            "provider_refund_id": result.get("id"),
            "status": result.get("status") or "PENDING",
            "raw": result,
        }

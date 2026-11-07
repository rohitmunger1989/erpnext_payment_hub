from __future__ import annotations

from urllib.parse import parse_qs, quote, urlparse

from erpnext_payment_hub.providers.base import BaseProvider, ProviderError


class UPaymentsProvider(BaseProvider):
    provider_name = "UPayments"

    def get_token(self):
        token = self.account.get_password("api_key", raise_exception=False)
        if not token:
            raise ProviderError("UPayments API Key / Token is missing.")
        return token

    def request(self, method, url, *, json_data=None, timeout=30):
        import base64
        import hashlib
        import hmac
        import json
        import time

        import requests

        secret = self.account.get_password("secret_key", raise_exception=False)
        if not secret:
            raise ProviderError("UPayments HMAC Secret Key is missing.")

        method = method.upper()
        parsed = urlparse(url)
        prefix = "/api/v1/"
        if not parsed.path.startswith(prefix):
            raise ProviderError("UPayments URL must contain /api/v1/.")

        endpoint = parsed.path[len(prefix):]
        body = (
            json.dumps(json_data, separators=(",", ":"), ensure_ascii=False)
            if json_data is not None else ""
        )
        timestamp = str(int(time.time()))
        signing_data = f"{timestamp}{method}{endpoint}{body}"
        signature = base64.b64encode(
            hmac.new(
                secret.encode("utf-8"),
                signing_data.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("ascii")

        headers = self.headers()
        headers.update({
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        })

        try:
            response = requests.request(
                method,
                url,
                data=body.encode("utf-8") if body else None,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise ProviderError("UPayments connection failed.") from exc

        try:
            result = response.json()
        except ValueError:
            raise ProviderError(
                f"UPayments returned non-JSON (HTTP {response.status_code})."
            )

        if not response.ok:
            raise ProviderError(
                f"UPayments API error {response.status_code}: {result}"
            )
        if not isinstance(result, dict):
            raise ProviderError("UPayments returned an unexpected response format.")
        return result


    def base_url(self):
        if self.account.base_url_override:
            return self.account.base_url_override.rstrip("/")
        if self.account.test_mode:
            return "https://sandboxapi.upayments.com/api/v1"
        raise ProviderError(
            "Set Base URL Override on the UPayments provider account for production."
        )

    def _source(self, payment_method):
        method = (payment_method or "ALL").upper().replace(" ", "_")
        return {
            "KNET": "knet",
            "CARD": "cc",
            "CREDIT_CARD": "cc",
            "DEBIT_CARD": "cc",
            "APPLE_PAY": "apple-pay",
            "APPLE_PAY_KNET": "apple-pay-knet",
            "GOOGLE_PAY": "google-pay",
            "SAMSUNG_PAY": "samsung-pay",
        }.get(method)


    def _parse_status_response(self, result, transaction=None):
        data = result.get("data") or result.get("Data") or {}
        paymit = data.get("payMit") or data.get("paymit") or {}
        paymit_order = paymit.get("order") or {}
        txn_data = (
            data.get("transaction")
            or data.get("transactionData")
            or data.get("transaction_data")
            or {}
        )

        payment_status = (
            txn_data.get("result")
            or paymit_order.get("status")
            or txn_data.get("status")
            or paymit.get("result")
        )

        existing_order = getattr(transaction, "provider_order_id", None) if transaction else None
        existing_session = getattr(transaction, "provider_session_id", None) if transaction else None

        return {
            "status": payment_status,
            "provider_transaction_id": txn_data.get("payment_id") or txn_data.get("paymentId"),
            "provider_order_id": txn_data.get("order_id") or txn_data.get("orderId") or existing_order,
            "provider_payment_id": txn_data.get("payment_id") or txn_data.get("paymentId"),
            "provider_tracking_id": txn_data.get("track_id") or txn_data.get("trackId"),
            "provider_session_id": (
                txn_data.get("session_id")
                or txn_data.get("sessionId")
                or existing_session
            ),
            "payment_type": txn_data.get("payment_type") or txn_data.get("paymentType") or txn_data.get("payment_method"),
            "requested_order_id": txn_data.get("merchant_requested_order_id") or txn_data.get("requested_order_id") or txn_data.get("requestedOrderId"),
            "raw": result,
        }

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
        # UPayments/KNET requires a unique merchant transaction reference for
        # every payment attempt.  A POS Payment Session (PPS) can have several
        # electronic allocations (PPA) when the cashier retries an abandoned or
        # expired link, so using the PPS name for every charge can reuse the same
        # merchant reference.  Prefer the allocation/attempt reference while
        # retaining the PPS in the description and extra data for audit.
        pos_context = pos_context or {}
        attempt_reference = (
            pos_context.get("pos_payment_allocation")
            or pos_context.get("attempt_reference")
            or reference_name
        )

        payload = {
            "order": {
                "id": str(attempt_reference)[:40],
                "reference": str(attempt_reference),
                "description": f"{reference_doctype} {reference_name}",
                "currency": currency,
                "amount": float(amount),
            },
            "language": "en",
            "tokens": {},
            "reference": {"id": str(attempt_reference)[:35]},
            "customer": {
                "uniqueId": reference_name[:50],
                "name": (customer.get("name") or "Customer")[:50],
                "email": (customer.get("email") or "")[:50],
                "mobile": (
                    f"+{customer.get('phone_country_code') or '965'}{customer.get('phone')}"
                    if customer.get("phone")
                    else ""
                ),
            },
            "customerExtraData": (
                f"{reference_doctype}:{reference_name}|attempt:{attempt_reference}"
            ),
            "returnUrl": return_url,
            "cancelUrl": cancel_url or return_url,
            "notificationUrl": (webhook_url or return_url).replace(" ", "%20"),
        }

        source = self._source(payment_method)
        if source and self.account.white_label:
            payload["paymentGateway"] = {"src": source}

        result = self.request(
            "POST",
            f"{self.base_url()}/charge",
            json_data=payload,
        )

        data = result.get("data") or result.get("Data") or {}
        payment_url = (
            data.get("link")
            or data.get("payment_url")
            or data.get("paymentURL")
            or data.get("url")
        )

        if not payment_url:
            raise ProviderError(
                "UPayments did not return a payment link. "
                f"Provider message: {result.get('message') or result.get('error') or 'No message returned'}"
            )

        session_id = None
        if payment_url:
            try:
                session_id = parse_qs(urlparse(payment_url).query).get("session_id", [None])[0]
            except Exception:
                session_id = None

        return {
            "provider_transaction_id": data.get("payment_id") or data.get("paymentId"),
            "provider_order_id": data.get("order_id") or data.get("orderId") or str(attempt_reference),
            "provider_payment_id": data.get("payment_id") or data.get("paymentId"),
            "provider_tracking_id": data.get("track_id") or data.get("trackId"),
            "provider_session_id": session_id,
            "status": data.get("result") or data.get("status") or "INITIATED",
            "payment_url": payment_url,
            "raw": result,
        }

    def get_payment_status(self, transaction):
        track_id = transaction.provider_tracking_id
        session_id = getattr(transaction, "provider_session_id", None)

        if track_id:
            url = f"{self.base_url()}/get-payment-status/{quote(str(track_id), safe='')}"
        elif session_id:
            url = f"{self.base_url()}/get-payment-status?session_id={quote(str(session_id), safe='')}"
        else:
            raise ProviderError("UPayments track_id/session_id is missing.")

        result = self.request("GET", url)
        return self._parse_status_response(result, transaction=transaction)


    def refund(self, transaction, amount, reason=None, retry_key=None):
        if self.account.test_mode:
            raise ProviderError(
                "UPayments does not process refunds in Sandbox/Test Mode. "
                "Use a live captured transaction with the production API account "
                "to test full or partial refunds."
            )

        if not transaction.provider_order_id:
            raise ProviderError("UPayments original orderId is missing.")

        payload = {
            "orderId": transaction.provider_order_id,
            "totalPrice": float(amount),
            "reference": (
                f"{transaction.reference_name or transaction.name}-R{int(retry_key)}"
                if retry_key
                else (transaction.reference_name or transaction.name)
            ),
            "notifyUrl": (
                f"{__import__('frappe').utils.get_url()}/api/method/"
                "erpnext_payment_hub.webhook.upayments_refund"
                f"?provider_account={self.account.name}"
            ),
            "refundReason": reason or "ERPNext customer refund",
        }

        result = self.request(
            "POST",
            f"{self.base_url()}/create-refund",
            json_data=payload,
        )
        data = result.get("data") or {}
        return {
            "provider_refund_id": data.get("refundOrderId") or data.get("orderId"),
            "provider_tracking_id": data.get("refundArn"),
            "status": "REQUESTED" if result.get("status") else "FAILED",
            "raw": result,
        }

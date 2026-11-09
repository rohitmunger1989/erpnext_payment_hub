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
        if parsed.query:
            endpoint += "?" + parsed.query
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
            error = ProviderError(f"UPayments API error {response.status_code}: {result}")
            error.http_status = response.status_code
            error.response_payload = result
            raise error
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


    @staticmethod
    def _link_session_id(transaction):
        if not transaction:
            return None
        payment_url = getattr(transaction, "payment_url", None) or ""
        return (
            parse_qs(urlparse(payment_url).query).get("session_id", [None])[0]
            or getattr(transaction, "provider_session_id", None)
        )

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
        existing_session = self._link_session_id(transaction)
        terminal = txn_data.get("isTerminalStatus")
        if terminal is None:
            terminal = data.get("isTerminalStatus")
        # Only an explicit boolean closes an unpaid session. A failed card/KNET
        # attempt may leave the original payment link available for another try.
        raw_status = str(payment_status or "").strip().upper().replace(" ", "_")
        if raw_status in {"CAPTURED", "SUCCESS", "PAID", "COMPLETED", "REFUNDED"}:
            normalized_status = raw_status
        elif terminal is True:
            if not raw_status or raw_status in {
                "PENDING", "INITIATED", "AUTHORIZED", "ACCEPTED", "REQUESTED",
                "IN_PROGRESS", "INPROGRESS",
            }:
                raise ProviderError("UPayments returned an inconsistent terminal status.")
            normalized_status = "FAILED"
        else:
            normalized_status = "PENDING"

        return {
            "status": normalized_status,
            "provider_attempt_status": payment_status,
            "provider_is_terminal": terminal if isinstance(terminal, bool) else None,
            "provider_transaction_id": txn_data.get("payment_id") or txn_data.get("paymentId"),
            "provider_order_id": existing_order or txn_data.get("order_id") or txn_data.get("orderId"),
            "provider_payment_id": txn_data.get("payment_id") or txn_data.get("paymentId"),
            "provider_tracking_id": txn_data.get("track_id") or txn_data.get("trackId"),
            # Never overwrite the checkout session with an individual attempt ID.
            "provider_session_id": existing_session,
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
        session_id = self._link_session_id(transaction)

        # A confirmed capture on a known attempt must not be hidden by a stale
        # session response. For unpaid attempts, verify the original session.
        attempt = None
        if track_id:
            result = self.request(
                "GET",
                f"{self.base_url()}/get-payment-status/{quote(str(track_id), safe='')}",
            )
            attempt = self._parse_status_response(result, transaction=transaction)
            if attempt["status"] in {"CAPTURED", "SUCCESS", "PAID", "COMPLETED", "REFUNDED"}:
                return attempt

        if session_id:
            result = self.request(
                "GET",
                f"{self.base_url()}/get-payment-status?session_id={quote(str(session_id), safe='')}",
            )
            return self._parse_status_response(result, transaction=transaction)
        if attempt is not None:
            # A transaction-attempt failure alone cannot close the checkout link.
            if attempt["status"] == "FAILED":
                attempt["status"] = "PENDING"
            return attempt
        raise ProviderError("UPayments track_id/session_id is missing.")


    @staticmethod
    def insufficient_balance_rejection(payload, http_status=None):
        """Recognize an explicit refusal, never a queued/partly accepted refund."""
        if not isinstance(payload, dict):
            return False
        if http_status is not None and http_status not in (200, 201, 400, 402, 422):
            return False
        status = payload.get("status")
        if status is not False and not (
            status is None and http_status in (400, 402, 422)
        ):
            return False
        # Even with a failure flag, any accepted refund/request identifier makes
        # the outcome uncertain. Never authorize a duplicate on that response.
        stack = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                for key, value in item.items():
                    compact = str(key).lower().replace("_", "")
                    if compact in {
                        "refundorderid", "refundid", "refundrequestid",
                        "refunrequestid", "refundarn", "generatedinvoiceid",
                    } and value:
                        return False
                    if compact in {"generated", "accepted", "success"} and value:
                        return False
                    if isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(item, list):
                stack.extend(item)
        phrases = {
            "insufficient balance", "insufficient wallet balance",
            "insufficient merchant balance", "insufficient merchant wallet balance",
            "insufficient funds", "not enough balance",
        }
        for key in ("message", "error", "code", "error_code"):
            value = payload.get(key)
            if not isinstance(value, str):
                continue
            normalized = " ".join(value.lower().replace("_", " ").replace("-", " ").split()).strip(" .!:;")
            if normalized in phrases:
                return True
        return False

    @staticmethod
    def _balance_rejection_error(payload, transaction, amount, http_status=None):
        try:
            import json
            import frappe

            frappe.log_error(
                title=f"UPayments refund balance rejection: {transaction.name}",
                message=json.dumps({
                    "original_transaction": transaction.name,
                    "requested_amount": str(amount),
                    "provider_response": payload,
                }, ensure_ascii=False, default=str),
            )
        except Exception:
            pass
        error = ProviderError(
            "Insufficient UPayments wallet balance. "
            "Top up your merchant wallet, then click Retry Refund."
        )
        error.refund_rejection_code = "INSUFFICIENT_MERCHANT_WALLET_BALANCE"
        error.response_payload = payload
        error.http_status = http_status
        return error

    def refund(self, transaction, amount, reason=None, retry_key=None):
        if self.account.test_mode:
            raise ProviderError(
                "UPayments does not process refunds in Sandbox/Test Mode. "
                "Use a live captured transaction with the production API account "
                "to test full or partial refunds."
            )

        # The merchant reference used to locate callbacks is not the provider's
        # refund order ID. Obtain it from a verified original-payment response.
        verified = self.get_payment_status(transaction)
        if verified.get("status") not in {"CAPTURED", "SUCCESS", "PAID", "COMPLETED"}:
            raise ProviderError("UPayments original payment is not confirmed captured.")
        payment_data = (verified.get("raw") or {}).get("data") or {}
        payment_txn = payment_data.get("transaction") or payment_data.get("transactionData") or payment_data.get("transaction_data") or {}
        refund_order_id = payment_txn.get("refund_order_id") or payment_txn.get("order_id") or payment_txn.get("orderId")
        if not refund_order_id:
            raise ProviderError("UPayments verified refund order ID is missing.")

        payload = {
            "orderId": refund_order_id,
            "totalPrice": float(amount),
            "reference": (
                f"{transaction.reference_name or transaction.name}-R{int(retry_key)}"
                if retry_key
                else (transaction.reference_name or transaction.name)
            ),
            "notifyUrl": (
                f"{__import__('frappe').utils.get_url()}/api/method/"
                "erpnext_payment_hub.webhook.upayments_refund"
                f"?provider_account={quote(self.account.name, safe='')}"
            ),
            "refundReason": reason or "ERPNext customer refund",
        }

        try:
            result = self.request(
                "POST",
                f"{self.base_url()}/create-refund",
                json_data=payload,
            )
        except ProviderError as exc:
            response_payload = getattr(exc, "response_payload", None)
            http_status = getattr(exc, "http_status", None)
            if self.insufficient_balance_rejection(response_payload, http_status):
                raise self._balance_rejection_error(
                    response_payload, transaction, amount, http_status
                ) from exc
            raise
        if self.insufficient_balance_rejection(result):
            raise self._balance_rejection_error(result, transaction, amount)
        data = result.get("data") or {}
        if result.get("status") is not True or not data.get("refundOrderId") or not data.get("orderId"):
            # Preserve the actual provider response without authorizing another POST.
            # Audit logging must never turn an accepted refund into a local failure.
            try:
                import json
                import frappe

                frappe.log_error(
                    title=f"UPayments refund response review: {transaction.name}",
                    message=json.dumps({
                        "original_transaction": transaction.name,
                        "requested_amount": str(amount),
                        "provider_response": result,
                    }, ensure_ascii=False, default=str),
                )
            except Exception:
                pass
            provider_message = result.get("message") or result.get("error") or "No message returned"
            error = ProviderError(
                "UPayments refund outcome is unconfirmed. "
                f"Provider status: {result.get('status')!r}; message: {provider_message}. "
                "Response identifiers are incomplete; review the provider before retrying."
            )
            error.response_payload = result
            raise error
        return {
            "provider_refund_id": data["refundOrderId"],
            "provider_order_id": data["orderId"],
            "provider_tracking_id": data.get("refundArn"),
            "status": "REQUESTED",
            "raw": result,
        }

    def get_refund_status(self, transaction):
        import json
        from decimal import Decimal, InvalidOperation

        # Recover identifiers from the original creation response for older rows.
        raw = getattr(transaction, "response_json", None) or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = {}
        created = (raw.get("data") or {}) if isinstance(raw, dict) else {}
        if not isinstance(created, dict):
            created = {}
        order_id = created.get("orderId") or getattr(transaction, "provider_order_id", None)
        refund_id = getattr(transaction, "provider_refund_id", None) or created.get("refundOrderId")
        if not order_id or not refund_id:
            raise ProviderError("UPayments refund identifiers are missing; review the provider before retrying.")
        result = self.request("GET", f"{self.base_url()}/check-refund/{quote(str(order_id), safe='')}")
        if result.get("status") is not True:
            raise ProviderError("UPayments did not confirm the refund status lookup.")
        # Match this exact refund, never another partial refund or the top-level
        # HTTP/API success flag. Unknown layouts/statuses remain unconfirmed.
        stack = [result.get("data")]
        matches = []
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                candidate = value.get("refundOrderId") or value.get("refund_order_id")
                if candidate and str(candidate) == str(refund_id):
                    matches.append(value)
                stack.extend(item for item in value.values() if isinstance(item, (dict, list)))
            elif isinstance(value, list):
                stack.extend(value)
        if len(matches) != 1:
            raise ProviderError("UPayments did not uniquely identify this refund in its status response.")
        item = matches[0]
        status = str(item.get("refund_status") or item.get("refundStatus") or item.get("status") or item.get("result") or "").strip().upper()
        if status == "REFUNDED":
            normalized = "REFUNDED"
        elif status in {"FAILED", "REJECTED", "CANCELED", "CANCELLED"}:
            normalized = "FAILED"
        elif status in {"INITIATED", "PENDING", "PROCESSING", "REQUESTED", "APPROVED"}:
            normalized = "PENDING"
        else:
            raise ProviderError("UPayments returned an unrecognized refund status; no completion was recorded.")
        amount = item.get("refundedAmount", item.get("totalPrice", item.get("amount")))
        if amount is not None:
            try:
                if abs(Decimal(str(amount)) - Decimal(str(transaction.amount))) > Decimal("0.0005"):
                    raise ProviderError("UPayments refund amount does not match this refund allocation.")
            except InvalidOperation as exc:
                raise ProviderError("UPayments returned an invalid refund amount.") from exc
        return {
            "status": normalized,
            "provider_refund_id": refund_id,
            "provider_order_id": order_id,
            "raw": result,
        }

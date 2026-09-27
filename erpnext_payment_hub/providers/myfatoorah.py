from __future__ import annotations

from erpnext_payment_hub.providers.base import BaseProvider, ProviderError


import frappe
class MyFatoorahProvider(BaseProvider):
    provider_name = "MyFatoorah"

    def base_url(self):
        if self.account.base_url_override:
            return self.account.base_url_override.rstrip("/")
        if self.account.test_mode:
            return "https://apitest.myfatoorah.com"
        return "https://api.myfatoorah.com"

    def _payment_method(self, payment_method):
        method = (payment_method or "ALL").upper().replace(" ", "_")
        mapping = {
            "KNET": "KNET",
            "CARD": "CARD",
            "CREDIT_CARD": "CARD",
            "DEBIT_CARD": "CARD",
            "APPLE_PAY": "APPLE_PAY",
            "GOOGLE_PAY": "GOOGLE_PAY",
        }
        return mapping.get(method)

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
        payload = {
            "Order": {"Amount": float(amount)},
            "OperationType": "PAY",
            "IntegrationUrls": {"Redirection": return_url},
            "Language": "EN",
            "MetaData": {
                "erpnext_doctype": reference_doctype,
                "erpnext_docname": reference_name,
                "currency": currency,
            },
        }

        method = self._payment_method(payment_method)
        if method:
            payload["PaymentMethod"] = method

        customer_payload = {}
        if customer.get("name"):
            customer_payload["Name"] = customer["name"]
        if customer.get("email"):
            customer_payload["Email"] = customer["email"]
        if customer.get("phone"):
            customer_payload["Mobile"] = {
                "CountryCode": f"+{customer.get('phone_country_code') or '965'}",
                "Number": customer["phone"],
            }
        customer_payload["Reference"] = reference_name
        payload["Customer"] = customer_payload

        result = self.request(
            "POST",
            f"{self.base_url()}/v3/payments",
            json_data=payload,
        )

        data = result.get("Data") or {}
        return {
            "provider_transaction_id": None,
            "provider_order_id": str(data.get("InvoiceId") or ""),
            "provider_payment_id": data.get("PaymentId"),
            "provider_tracking_id": None,
            "status": "CAPTURED" if data.get("PaymentCompleted") else "INITIATED",
            "payment_url": data.get("PaymentURL"),
            "raw": result,
        }

    def get_payment_status(self, transaction):
        """Return the current MyFatoorah payment status.

        MyFatoorah may initially return a temporary/in-progress PaymentId when
        the hosted KNET payment is created. After payment, the same InvoiceId
        can contain a different final successful PaymentId.

        Therefore:
        1. Try the stored PaymentId when available.
        2. If it is already successful, use it.
        3. If it is invalid/stale/pending, recover through InvoiceId.
        4. Locate the successful invoice transaction.
        5. Verify its final PaymentId through the v3 endpoint.

        This recovery is MyFatoorah-specific and does not affect Tap or
        UPayments.
        """
        payment_id = transaction.provider_payment_id
        payment_result = None

        # First try the PaymentId already stored locally.
        #
        # MyFatoorah can return an initial/in-progress PaymentId which later
        # becomes invalid for v3 lookup after KNET creates the final successful
        # transaction. Do not stop recovery merely because that stale ID exists.
        if payment_id:
            try:
                payment_result = self.get_payment_status_by_payment_id(payment_id)

                payment_status = str(
                    payment_result.get("status") or ""
                ).strip().upper()

                if payment_status in {
                    "SUCCESS",
                    "SUCCSS",
                    "CAPTURED",
                    "PAID",
                }:
                    return payment_result

                # A valid but non-final PaymentId (for example INPROGRESS)
                # must still be reconciled against InvoiceId because the same
                # invoice may now contain a newer successful KNET transaction.

            except ProviderError:
                # A stale/pre-final PaymentId can return HTTP 400 Invalid data.
                # Continue with the immutable InvoiceId recovery path.
                payment_result = None

        invoice_id = transaction.provider_order_id

        if not invoice_id:
            # If PaymentId inquiry itself worked but is merely pending and no
            # InvoiceId exists, return the real provider result rather than
            # inventing a different state.
            if payment_result:
                return payment_result

            raise ProviderError(
                "MyFatoorah PaymentId and InvoiceId are both missing; payment status "
                "cannot be recovered automatically."
            )

        # InvoiceId is stable across the hosted checkout lifecycle and can be
        # used to discover the final successful transaction/PaymentId.
        result = self.request(
            "POST",
            f"{self.base_url()}/v2/GetPaymentStatus",
            json_data={
                "Key": str(invoice_id),
                "KeyType": "InvoiceId",
            },
        )

        data = result.get("Data") or {}
        transactions = data.get("InvoiceTransactions") or []

        successful = None

        # MyFatoorah v2 currently returns the successful KNET status as
        # "Succss" (without the second 'e'), while other responses/endpoints
        # can use SUCCESS/PAID/CAPTURED.
        successful_statuses = {
            "SUCCESS",
            "SUCCSS",
            "CAPTURED",
            "PAID",
        }

        for row in transactions:
            raw_status = (
                row.get("TransactionStatus")
                or row.get("Status")
                or ""
            )

            if str(raw_status).strip().upper() in successful_statuses:
                successful = row
                break

        txn = successful or (transactions[-1] if transactions else {})

        recovered_payment_id = (
            txn.get("PaymentId")
            or txn.get("PaymentID")
            or txn.get("paymentId")
        )

        # When InvoiceId reveals the final successful PaymentId, verify that
        # exact payment through MyFatoorah v3 and return the canonical result.
        if recovered_payment_id:
            return self.get_payment_status_by_payment_id(
                str(recovered_payment_id)
            )

        return {
            "status": (
                txn.get("TransactionStatus")
                or txn.get("Status")
                or data.get("InvoiceStatus")
            ),
            "provider_transaction_id": str(
                txn.get("TransactionId")
                or txn.get("TransactionID")
                or txn.get("Id")
                or ""
            ),
            "provider_order_id": str(
                data.get("InvoiceId")
                or invoice_id
            ),
            "provider_payment_id": None,
            "provider_tracking_id": (
                txn.get("TrackId")
                or txn.get("ReferenceId")
                or txn.get("ReferenceID")
            ),
            "payment_type": (
                txn.get("PaymentGateway")
                or txn.get("PaymentMethod")
            ),
            "raw": result,
        }

    def get_payment_status_by_payment_id(self, payment_id):
        result = self.request("GET", f"{self.base_url()}/v3/payments/{payment_id}")
        data = result.get("Data") or {}
        txn = data.get("Transaction") or {}
        invoice = data.get("Invoice") or {}
        return {
            "status": txn.get("Status") or invoice.get("Status"),
            "provider_transaction_id": str(txn.get("Id") or ""),
            "provider_order_id": str(invoice.get("Id") or ""),
            "provider_payment_id": txn.get("PaymentId") or payment_id,
            "provider_tracking_id": txn.get("TrackId"),
            "payment_type": txn.get("PaymentMethod"),
            "raw": result,
        }

    def _get_refund_status_by_invoice(self, invoice_id, timeout=60):
        return self.request(
            "POST",
            f"{self.base_url()}/v2/GetRefundStatus",
            json_data={"KeyType": "InvoiceId", "Key": str(invoice_id)},
            timeout=timeout,
        )

    def _refund_row_to_normalized(self, row, raw):
        status = row.get("RefundStatus") or "PENDING"
        return {
            "provider_refund_id": str(row.get("RefundId") or ""),
            "provider_tracking_id": row.get("RefundReference"),
            "status": status,
            "raw": raw,
        }

    def get_refund_status(self, transaction):
        if transaction.provider_refund_id:
            key_type = "RefundId"
            key = transaction.provider_refund_id
        elif transaction.provider_tracking_id:
            key_type = "RefundReference"
            key = transaction.provider_tracking_id
        else:
            original = frappe.get_doc(
                "Gateway Transaction", transaction.original_transaction
            )
            key_type = "InvoiceId"
            key = original.provider_order_id

        result = self.request(
            "POST",
            f"{self.base_url()}/v2/GetRefundStatus",
            json_data={"KeyType": key_type, "Key": str(key)},
            timeout=60,
        )
        rows = (result.get("Data") or {}).get("RefundStatusResult") or []

        if transaction.provider_refund_id:
            for row in rows:
                if str(row.get("RefundId") or "") == str(transaction.provider_refund_id):
                    return self._refund_row_to_normalized(row, result)

        if transaction.provider_tracking_id:
            for row in rows:
                if str(row.get("RefundReference") or "") == str(transaction.provider_tracking_id):
                    return self._refund_row_to_normalized(row, result)

        if rows:
            return self._refund_row_to_normalized(rows[-1], result)

        return {
            "status": "PENDING",
            "raw": result,
        }

    def refund(self, transaction, amount, reason=None, retry_key=None):
        if transaction.provider_payment_id:
            key_type = "PaymentId"
            key = transaction.provider_payment_id
        elif transaction.provider_order_id:
            key_type = "InvoiceId"
            key = transaction.provider_order_id
        else:
            raise ProviderError("MyFatoorah PaymentId/InvoiceId is missing.")

        amount = float(amount)
        before = float(transaction.refunded_amount or 0)
        # Deterministic for a retry before local refunded_amount is reserved.
        external_identifier = f"EPH-{transaction.name}-{before:.3f}-{amount:.3f}"
        if retry_key:
            external_identifier = f"{external_identifier}-R{int(retry_key)}"

        # SAFETY: before another MakeRefund POST, query the invoice for an
        # already-created request. This also recovers legacy v0.1.15 timeouts,
        # which used the original transaction name as ExternalIdentifier.
        if transaction.provider_order_id:
            try:
                existing = self._get_refund_status_by_invoice(
                    transaction.provider_order_id, timeout=60
                )
                rows = (existing.get("Data") or {}).get("RefundStatusResult") or []
                for row in rows:
                    row_amount = float(row.get("Amount") or 0)
                    row_external = str(row.get("ExternalIdentifier") or "")
                    if (
                        abs(row_amount - amount) < 0.0005
                        and row_external in {external_identifier, transaction.name}
                    ):
                        return self._refund_row_to_normalized(row, existing)
            except ProviderError:
                # Inquiry failure should not silently convert into a refund success.
                # Continue to MakeRefund only if the API itself is reachable enough
                # to process the request below.
                pass

        payload = {
            "KeyType": key_type,
            "Key": key,
            "ServiceChargeOnCustomer": False,
            "Amount": amount,
            "Comment": reason or "ERPNext customer refund",
            "ExternalIdentifier": external_identifier,
            "AmountDeductedFromSupplier": 0,
        }

        try:
            result = self.request(
                "POST",
                f"{self.base_url()}/v2/MakeRefund",
                json_data=payload,
                timeout=90,
            )
        except ProviderError as exc:
            # A timeout after POST may still mean MyFatoorah accepted the refund.
            # Query before surfacing failure so the caller never blindly retries.
            if "timed out" in str(exc).lower() and transaction.provider_order_id:
                recovered = self._get_refund_status_by_invoice(
                    transaction.provider_order_id, timeout=60
                )
                rows = (recovered.get("Data") or {}).get("RefundStatusResult") or []
                for row in rows:
                    row_amount = float(row.get("Amount") or 0)
                    if (
                        abs(row_amount - amount) < 0.0005
                        and str(row.get("ExternalIdentifier") or "")
                        == external_identifier
                    ):
                        return self._refund_row_to_normalized(row, recovered)
            raise

        data = result.get("Data") or {}

        return {
            "provider_refund_id": str(data.get("RefundId") or ""),
            "provider_tracking_id": data.get("RefundReference"),
            "status": "REQUESTED" if result.get("IsSuccess") else "FAILED",
            "raw": result,
        }

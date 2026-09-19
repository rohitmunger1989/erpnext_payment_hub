from __future__ import annotations

import frappe

from erpnext_payment_hub.gateway import (
    get_provider,
    get_provider_account,
    update_transaction_from_status,
)


def _json():
    return frappe.request.get_json(silent=True) or dict(frappe.form_dict)


def _find_transaction(filters):
    name = frappe.db.get_value("Gateway Transaction", filters, "name")
    return frappe.get_doc("Gateway Transaction", name) if name else None


@frappe.whitelist(allow_guest=True)
def tap(provider_account=None):
    payload = _json()
    charge_id = payload.get("id")
    if not provider_account or not charge_id:
        return {"ok": False}

    doc = _find_transaction(
        {
            "provider_account": provider_account,
            "provider_transaction_id": charge_id,
            "transaction_type": "Payment",
        }
    )
    if not doc:
        return {"ok": False, "reason": "transaction_not_found"}

    # Do not trust the incoming payload alone. Re-query Tap server-to-server.
    account = get_provider_account(provider_account)
    normalized = get_provider(account).get_payment_status(doc)
    update_transaction_from_status(doc, normalized)
    return {"ok": True, "transaction": doc.name, "status": doc.status}


@frappe.whitelist(allow_guest=True)
def myfatoorah(provider_account=None):
    payload = _json()
    data = payload.get("Data") if isinstance(payload.get("Data"), dict) else {}
    transaction = data.get("Transaction") if isinstance(data.get("Transaction"), dict) else {}

    payment_id = (
        payload.get("paymentId")
        or payload.get("PaymentId")
        or payload.get("PaymentID")
        or transaction.get("PaymentId")
        or transaction.get("PaymentID")
    )
    if not provider_account or not payment_id:
        return {"ok": False}

    account = get_provider_account(provider_account)
    provider = get_provider(account)
    normalized = provider.get_payment_status_by_payment_id(payment_id)

    invoice_id = normalized.get("provider_order_id")
    doc = _find_transaction(
        {
            "provider_account": provider_account,
            "provider_order_id": invoice_id,
            "transaction_type": "Payment",
        }
    )
    if not doc:
        return {"ok": False, "reason": "transaction_not_found"}

    update_transaction_from_status(doc, normalized)
    return {"ok": True, "transaction": doc.name, "status": doc.status}


@frappe.whitelist(allow_guest=True)
def upayments(provider_account=None):
    payload = _json()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    track_id = data.get("track_id") or data.get("trackId")
    requested_order_id = (
        data.get("requested_order_id")
        or data.get("requestedOrderId")
        or data.get("order_id")
        or data.get("orderId")
    )

    if not provider_account:
        return {"ok": False, "reason": "provider_account_missing"}

    doc = None

    if track_id:
        doc = _find_transaction(
            {
                "provider_account": provider_account,
                "provider_tracking_id": track_id,
                "transaction_type": "Payment",
            }
        )

    if not doc and requested_order_id:
        doc = _find_transaction(
            {
                "provider_account": provider_account,
                "provider_order_id": requested_order_id,
                "transaction_type": "Payment",
            }
        )

    if not doc:
        return {"ok": False, "reason": "transaction_not_found"}

    # Save callback references first, then verify status server-to-server.
    if track_id:
        doc.provider_tracking_id = track_id
    if data.get("payment_id") or data.get("paymentId"):
        doc.provider_payment_id = data.get("payment_id") or data.get("paymentId")
        doc.provider_transaction_id = doc.provider_payment_id
    doc.save(ignore_permissions=True)

    account = get_provider_account(provider_account)
    normalized = get_provider(account).get_payment_status(doc)
    update_transaction_from_status(doc, normalized)

    return {"ok": True, "transaction": doc.name, "status": doc.status}


@frappe.whitelist(allow_guest=True)
def tap_refund(provider_account=None):
    payload = _json()
    refund_id = payload.get("id")
    if not provider_account or not refund_id:
        return {"ok": False}

    doc = _find_transaction(
        {
            "provider_account": provider_account,
            "provider_refund_id": refund_id,
            "transaction_type": "Refund",
        }
    )
    if not doc:
        return {"ok": False, "reason": "refund_transaction_not_found"}

    # Refund webhooks are hints only. Re-query Tap before changing local state.
    account = get_provider_account(provider_account)
    normalized = get_provider(account).get_refund_status(doc)
    update_transaction_from_status(doc, normalized)
    return {"ok": True, "transaction": doc.name, "status": doc.status}


@frappe.whitelist(allow_guest=True)
def upayments_refund(provider_account=None):
    # Phase 1 stores refund requests; status polling/refund webhook sync is Phase 2.
    return {"ok": True}

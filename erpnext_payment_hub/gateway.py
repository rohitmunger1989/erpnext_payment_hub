from __future__ import annotations

import json

import frappe
from frappe.exceptions import TimestampMismatchError
from frappe.utils import flt

from erpnext_payment_hub.providers.base import ProviderError
from erpnext_payment_hub.providers.myfatoorah import MyFatoorahProvider
from erpnext_payment_hub.providers.tap import TapProvider
from erpnext_payment_hub.providers.upayments import UPaymentsProvider


PROVIDERS = {
    "Tap Payments": TapProvider,
    "MyFatoorah": MyFatoorahProvider,
    "UPayments": UPaymentsProvider,
}


def get_provider_account(account_name=None, *, for_new_transaction=False):
    if account_name:
        account = frappe.get_doc("Payment Provider Account", account_name)
    else:
        settings = frappe.get_single("Payment Hub Settings")
        if not settings.default_provider:
            frappe.throw("Set Default Provider in Payment Hub Settings.")
        account = frappe.get_doc("Payment Provider Account", settings.default_provider)

    if for_new_transaction and account.status not in ("Active", "Test"):
        frappe.throw(
            f"Provider account {account.name} cannot accept new payments. "
            f"Current status: {account.status}"
        )

    return account


def get_provider(account):
    cls = PROVIDERS.get(account.provider)
    if not cls:
        raise ProviderError(f"Unsupported provider: {account.provider}")
    return cls(account)


def _normalize_kuwait_phone(value):
    """Return (country_code, local_number) for common Kuwait mobile formats."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if digits.startswith("00965"):
        digits = digits[5:]
    elif digits.startswith("965") and len(digits) > 8:
        digits = digits[3:]
    return "965", digits


def _customer_from_reference(reference_doctype, reference_name):
    customer = {
        "name": "Customer",
        "email": "",
        "phone": "",
        "phone_country_code": "965",
    }

    if not reference_doctype or not reference_name:
        return customer

    if not frappe.db.exists(reference_doctype, reference_name):
        return customer

    doc = frappe.get_doc(reference_doctype, reference_name)

    customer["name"] = (
        getattr(doc, "customer_name", None)
        or getattr(doc, "customer", None)
        or "Customer"
    )
    customer["email"] = (
        getattr(doc, "contact_email", None)
        or getattr(doc, "email_id", None)
        or ""
    )
    raw_phone = (
        getattr(doc, "contact_mobile", None)
        or getattr(doc, "mobile_no", None)
        or ""
    )
    country_code, phone = _normalize_kuwait_phone(raw_phone)
    customer["phone_country_code"] = country_code
    customer["phone"] = phone
    return customer


def normalize_status(provider, raw_status):
    if not isinstance(raw_status, str):
        raw_status = ""
    status = raw_status.upper().replace(" ", "_")

    success = {
        "CAPTURED",
        "SUCCESS",
        "PAID",
        "COMPLETED",
        "REFUNDED",
    }
    pending = {
        "INITIATED",
        "PENDING",
        "INPROGRESS",
        "IN_PROGRESS",
        "REQUESTED",
        "AUTHORIZED",
        "ACCEPTED",
    }
    failed = {
        "FAILED",
        "DECLINED",
        "CANCELLED",
        "CANCELED",
        "ABANDONED",
        "EXPIRED",
        "TIMED_OUT",
        "TIMEDOUT",
        "REJECTED",
        "RESTRICTED",
    }

    if status in success:
        return "Captured" if status != "REFUNDED" else "Refunded"
    if status in pending:
        return "Pending"
    if status in failed:
        return "Failed"
    return raw_status or "Pending"


def save_raw(doc, payload):
    doc.response_json = json.dumps(payload or {}, ensure_ascii=False, default=str)


def create_gateway_transaction(
    *,
    transaction_type,
    provider_account,
    reference_doctype=None,
    reference_name=None,
    original_transaction=None,
    payment_method=None,
    amount=0,
    currency="KWD",
    normalized=None,
    branch=None,
    pos_profile=None,
    payment_terminal=None,
    terminal_id=None,
    pos_station=None,
    computer_name=None,
    pos_payment_session=None,
    pos_payment_allocation=None,
):
    normalized = normalized or {}

    doc = frappe.new_doc("Gateway Transaction")
    doc.transaction_type = transaction_type
    doc.provider_account = provider_account.name
    doc.provider = provider_account.provider
    doc.reference_doctype = reference_doctype
    doc.reference_name = reference_name
    doc.original_transaction = original_transaction
    doc.payment_method = payment_method
    doc.amount = flt(amount, 3)
    doc.currency = currency
    doc.branch = branch
    doc.pos_profile = pos_profile
    doc.payment_terminal = payment_terminal
    doc.terminal_id = terminal_id
    doc.pos_station = pos_station
    doc.computer_name = computer_name
    doc.pos_payment_session = pos_payment_session
    doc.pos_payment_allocation = pos_payment_allocation

    doc.provider_transaction_id = normalized.get("provider_transaction_id")
    doc.provider_order_id = normalized.get("provider_order_id")
    doc.provider_payment_id = normalized.get("provider_payment_id")
    doc.provider_tracking_id = normalized.get("provider_tracking_id")
    doc.provider_session_id = normalized.get("provider_session_id")
    doc.provider_payment_type = normalized.get("payment_type")
    doc.provider_requested_order_id = normalized.get("requested_order_id")
    doc.provider_refund_id = normalized.get("provider_refund_id")
    doc.payment_url = normalized.get("payment_url")
    doc.status = normalize_status(provider_account.provider, normalized.get("status"))
    save_raw(doc, normalized.get("raw"))

    doc.insert(ignore_permissions=True)
    return doc


def _apply_normalized_status(doc, normalized):
    for field in (
        "provider_transaction_id",
        "provider_order_id",
        "provider_payment_id",
        "provider_tracking_id",
        "provider_session_id",
        "provider_refund_id",
    ):
        if normalized.get(field):
            setattr(doc, field, normalized[field])

    if normalized.get("payment_type"):
        doc.provider_payment_type = normalized.get("payment_type")
    if normalized.get("requested_order_id"):
        doc.provider_requested_order_id = normalized.get("requested_order_id")

    incoming_status = normalize_status(doc.provider, normalized.get("status"))

    # Provider callbacks, return redirects, manual checks, and the scheduler can
    # arrive at almost the same time. Never let a late Pending/Failed response
    # downgrade a transaction that has already been confirmed as Captured or
    # Refunded.
    if doc.status in ("Captured", "Refunded") and incoming_status in ("Pending", "Failed"):
        incoming_status = doc.status
    elif doc.status == "Refunded" and incoming_status == "Captured":
        incoming_status = "Refunded"

    doc.status = incoming_status
    save_raw(doc, normalized.get("raw"))


def update_transaction_from_status(doc, normalized):
    # Frappe uses optimistic locking (modified timestamp). Tap can hit both the
    # browser return URL and webhook within milliseconds, while POS reconciliation
    # may also be polling. Retry against the latest row instead of returning a
    # TimestampMismatchError to the customer.
    for attempt in range(3):
        _apply_normalized_status(doc, normalized)
        try:
            doc.save(ignore_permissions=True)
            break
        except TimestampMismatchError:
            if attempt >= 2:
                raise
            doc.reload()

    if getattr(doc, "pos_payment_allocation", None):
        try:
            from erpnext_payment_hub.pos.service import sync_allocation_from_gateway
            sync_allocation_from_gateway(doc)
        except Exception:
            frappe.log_error(
                title=f"Payment Hub POS sync failed: {doc.name}",
                message=frappe.get_traceback(),
            )

    if doc.transaction_type == "Refund":
        try:
            from erpnext_payment_hub.pos.refund import sync_refund_allocation_from_gateway
            sync_refund_allocation_from_gateway(doc)
        except Exception:
            frappe.log_error(
                title=f"Payment Hub refund sync failed: {doc.name}",
                message=frappe.get_traceback(),
            )
    return doc



def resolve_payment_terminal(
    *,
    provider_account,
    pos_profile=None,
    branch=None,
    company=None,
    warehouse=None,
):
    """Resolve the best terminal for the current POS context.

    Resolution order:
    1. Exact POS Profile match
    2. Branch default
    3. Branch match by priority
    4. Provider Account default_terminal_id fallback
    """

    filters = {
        "provider_account": provider_account.name,
        "enabled": 1,
    }

    if pos_profile:
        name = frappe.db.get_value(
            "Payment Terminal",
            {**filters, "pos_profile": pos_profile},
            "name",
            order_by="priority asc, modified desc",
        )
        if name:
            return frappe.get_doc("Payment Terminal", name)

    if branch:
        name = frappe.db.get_value(
            "Payment Terminal",
            {
                **filters,
                "branch": branch,
                "is_default_for_branch": 1,
            },
            "name",
            order_by="priority asc, modified desc",
        )
        if name:
            return frappe.get_doc("Payment Terminal", name)

        name = frappe.db.get_value(
            "Payment Terminal",
            {**filters, "branch": branch},
            "name",
            order_by="priority asc, modified desc",
        )
        if name:
            return frappe.get_doc("Payment Terminal", name)

    if provider_account.terminal_id:
        return frappe._dict(
            {
                "name": None,
                "terminal_name": "Provider Default",
                "terminal_id": provider_account.terminal_id,
                "device_id": None,
                "merchant_id_override": None,
                "provider_account": provider_account.name,
            }
        )

    return None


def get_pos_context(reference_doctype=None, reference_name=None):
    context = frappe._dict(
        {
            "company": None,
            "branch": None,
            "pos_profile": None,
            "warehouse": None,
        }
    )

    if not reference_doctype or not reference_name:
        return context

    if not frappe.db.exists(reference_doctype, reference_name):
        return context

    doc = frappe.get_doc(reference_doctype, reference_name)
    for field in ("company", "branch", "pos_profile", "warehouse"):
        if hasattr(doc, field):
            context[field] = getattr(doc, field, None)

    return context



def resolve_pos_station(*, computer_name=None, pairing_code=None, pos_profile=None, branch=None):
    """Resolve POS Station primarily from Windows computer name.

    Fallback order:
    1. Computer Name
    2. Pairing Code
    3. Unique POS Station for POS Profile/Branch
    """
    station_name = None

    if computer_name:
        computer_name = computer_name.strip().upper()
        station_name = frappe.db.get_value(
            "POS Station",
            {"computer_name": computer_name, "enabled": 1},
            "name",
        )

    if not station_name and pairing_code:
        candidates = frappe.get_all(
            "POS Station",
            filters={"enabled": 1},
            fields=["name"],
        )
        for row in candidates:
            station = frappe.get_doc("POS Station", row.name)
            try:
                stored = station.get_password("pairing_code")
            except Exception:
                stored = None
            if stored and secrets_compare(stored, pairing_code):
                station_name = row.name
                break

    if not station_name and pos_profile:
        rows = frappe.get_all(
            "POS Station",
            filters={"pos_profile": pos_profile, "enabled": 1},
            pluck="name",
            limit=2,
        )
        if len(rows) == 1:
            station_name = rows[0]

    if not station_name and branch:
        rows = frappe.get_all(
            "POS Station",
            filters={"branch": branch, "enabled": 1},
            pluck="name",
            limit=2,
        )
        if len(rows) == 1:
            station_name = rows[0]

    return frappe.get_doc("POS Station", station_name) if station_name else None


def secrets_compare(a, b):
    import hmac
    return hmac.compare_digest(str(a), str(b))


def touch_pos_station(station):
    if not station:
        return
    frappe.db.set_value(
        "POS Station",
        station.name,
        {
            "last_seen": frappe.utils.now(),
            "last_user": frappe.session.user if frappe.session else None,
            "last_ip": getattr(frappe.local, "request_ip", None),
        },
        update_modified=False,
    )

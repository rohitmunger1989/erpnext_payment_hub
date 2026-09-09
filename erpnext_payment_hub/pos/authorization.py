from __future__ import annotations

import hashlib
import hmac
import json
import secrets

import frappe
from frappe.utils import add_to_date, cint, flt, now_datetime
from frappe.utils.password import check_password


REFUND_APPROVER_ROLE = "Payment Hub Refund Approver"
REFUND_OVERRIDE_ROLE = "Payment Hub Refund Override"
AUDITOR_ROLE = "Payment Hub Auditor"


def _hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _roles(user: str) -> set[str]:
    try:
        return set(frappe.get_roles(user) or [])
    except Exception:
        return set()


def can_approve_refund(user: str) -> bool:
    if user == "Administrator":
        return True
    roles = _roles(user)
    return bool({"System Manager", REFUND_APPROVER_ROLE, REFUND_OVERRIDE_ROLE} & roles)


def can_override_refund(user: str) -> bool:
    if user == "Administrator":
        return True
    roles = _roles(user)
    return bool({"System Manager", REFUND_OVERRIDE_ROLE} & roles)


def _validate_approver_password(user: str, password: str, *, override: bool = False):
    if not user or not password:
        frappe.throw("Manager / admin username and password are required.")

    enabled = frappe.db.get_value("User", user, "enabled")
    if not enabled:
        frappe.throw("Invalid manager authorization credentials.", frappe.AuthenticationError)

    try:
        check_password(user, password)
    except Exception:
        frappe.throw("Invalid manager authorization credentials.", frappe.AuthenticationError)

    allowed = can_override_refund(user) if override else can_approve_refund(user)
    if not allowed:
        role = REFUND_OVERRIDE_ROLE if override else REFUND_APPROVER_ROLE
        frappe.throw(f"User {user} does not have {role} permission.", frappe.PermissionError)


def _source_names(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({str(item) for item in value if item})


@frappe.whitelist()
def authorize_pos_refund(
    approver,
    password,
    original_invoice,
    return_invoice,
    amount,
    reason,
    source_allocations=None,
    is_override=0,
    override_channel=None,
    override_mode_of_payment=None,
):
    """Verify a manager/admin password server-side and issue a short-lived refund token.

    The plaintext password and authorization token are never stored. The persistent
    DocType is an audit record containing only the token hash and approval metadata.
    """
    if frappe.session.user == "Guest":
        frappe.throw("Login is required.", frappe.PermissionError)

    is_override = bool(cint(is_override))
    _validate_approver_password(approver, password, override=is_override)

    if not frappe.db.exists("Sales Invoice", original_invoice):
        frappe.throw(f"Sales Invoice {original_invoice} does not exist.")
    return_doc = frappe.get_doc("Sales Invoice", return_invoice)
    if return_doc.docstatus != 0 or not return_doc.is_return or return_doc.return_against != original_invoice:
        frappe.throw("Refund authorization requires a Draft return invoice against the original sale.")

    amount = flt(amount, 3)
    if amount <= 0:
        frappe.throw("Authorized refund amount must be greater than zero.")
    reason = (reason or "").strip()
    if not reason:
        frappe.throw("Authorization reason is required.")

    settings = frappe.get_single("Payment Hub Settings")
    expiry_minutes = max(int(getattr(settings, "refund_authorization_minutes", 5) or 5), 1)

    if is_override:
        if not cint(getattr(settings, "allow_refund_method_override", 1)):
            frappe.throw("Refund method override is disabled in Payment Hub Settings.")
        cash_mode = getattr(settings, "cash_mode_of_payment", None) or "Cash"
        if override_channel != "Cash" or (override_mode_of_payment or cash_mode) != cash_mode:
            frappe.throw(
                "v0.5.0 only permits an audited manager override to the configured Cash refund method."
            )
        override_mode_of_payment = cash_mode

    token = secrets.token_urlsafe(32)
    doc = frappe.new_doc("Payment Hub Refund Authorization")
    doc.status = "Valid"
    doc.action = "Refund Override" if is_override else "Refund"
    doc.cashier_user = frappe.session.user
    doc.authorized_by = approver
    doc.authorized_at = now_datetime()
    doc.expires_at = add_to_date(doc.authorized_at, minutes=expiry_minutes)
    doc.original_invoice = original_invoice
    doc.return_invoice = return_invoice
    doc.amount = amount
    doc.currency = return_doc.currency
    doc.pos_profile = getattr(return_doc, "pos_profile", None)
    doc.source_allocations = json.dumps(_source_names(source_allocations))
    doc.is_override = 1 if is_override else 0
    doc.override_channel = override_channel if is_override else None
    doc.override_mode_of_payment = override_mode_of_payment if is_override else None
    doc.reason = reason
    doc.token_hash = _hash_token(token)
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "authorization": doc.name,
        "authorization_token": token,
        "authorized_by": approver,
        "action": doc.action,
        "expires_at": doc.expires_at,
    }


def validate_refund_authorization(
    token,
    *,
    original_invoice,
    return_invoice,
    amount,
    source_allocations=None,
    requires_override=False,
    override_channel=None,
    override_mode_of_payment=None,
):
    if not token:
        frappe.throw("Manager / admin authorization is required for this refund.", frappe.PermissionError)

    token_hash = _hash_token(token)
    name = frappe.db.get_value(
        "Payment Hub Refund Authorization", {"token_hash": token_hash}, "name"
    )
    if not name:
        frappe.throw("Refund authorization is invalid or has expired.", frappe.PermissionError)

    doc = frappe.get_doc("Payment Hub Refund Authorization", name)
    if not hmac.compare_digest(doc.token_hash or "", token_hash):
        frappe.throw("Refund authorization is invalid.", frappe.PermissionError)

    now = now_datetime()
    if doc.status == "Revoked":
        frappe.throw("Refund authorization was revoked.", frappe.PermissionError)
    if doc.status == "Expired" or (doc.status == "Valid" and doc.expires_at and doc.expires_at < now):
        if doc.status != "Expired":
            doc.status = "Expired"
            doc.save(ignore_permissions=True)
        frappe.throw("Refund authorization has expired. Ask the manager to authorize again.", frappe.PermissionError)

    if doc.cashier_user != frappe.session.user:
        frappe.throw("Refund authorization belongs to a different cashier session.", frappe.PermissionError)

    if doc.original_invoice != original_invoice or doc.return_invoice != return_invoice:
        frappe.throw("Refund authorization does not match this return invoice.", frappe.PermissionError)
    if flt(amount, 3) > flt(doc.amount, 3) + 0.0005:
        frappe.throw("Refund amount exceeds the manager-authorized amount.", frappe.PermissionError)

    expected_sources = _source_names(source_allocations)
    authorized_sources = _source_names(doc.source_allocations)
    if authorized_sources and any(source not in authorized_sources for source in expected_sources):
        frappe.throw("Refund authorization does not cover all selected payment sources.", frappe.PermissionError)

    if requires_override:
        if doc.action != "Refund Override" or not cint(doc.is_override):
            frappe.throw("Refund method override requires Refund Override authorization.", frappe.PermissionError)
        if doc.override_channel != override_channel or doc.override_mode_of_payment != override_mode_of_payment:
            frappe.throw("Refund override target does not match the manager authorization.", frappe.PermissionError)

    return doc


def mark_authorization_used(doc):
    if not doc:
        return
    doc.reload()
    if doc.status == "Valid":
        doc.status = "Used"
        doc.used_at = now_datetime()
        doc.save(ignore_permissions=True)

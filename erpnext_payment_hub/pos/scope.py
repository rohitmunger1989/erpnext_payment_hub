from __future__ import annotations

import json

import frappe
from frappe.utils import add_days, getdate, nowdate


CROSS_PROFILE_ROLES = {"System Manager", "Accounts Manager", "Payment Hub Auditor"}


def can_view_all_profiles(user=None):
    user = user or frappe.session.user
    if user == "Administrator":
        return True
    return bool(CROSS_PROFILE_ROLES.intersection(set(frappe.get_roles(user))))


def _doctype_exists(doctype):
    return bool(frappe.db.exists("DocType", doctype))


def _field_exists(doctype, fieldname):
    if not _doctype_exists(doctype):
        return False
    return bool(frappe.get_meta(doctype).has_field(fieldname))


def _parse_payload(value):
    if not value:
        return {}
    if isinstance(value, dict):
        payload = value
    elif isinstance(value, str):
        try:
            payload = json.loads(value)
        except Exception:
            return {}
    else:
        return {}
    if isinstance(payload.get("invoice"), dict):
        return payload["invoice"]
    if isinstance(payload.get("doc"), dict):
        return payload["doc"]
    return payload


def shift_from_payload(value):
    payload = _parse_payload(value)
    return payload.get("posa_pos_opening_shift") or payload.get("pos_opening_shift")


def get_shift_info(shift_name):
    if not shift_name or not _doctype_exists("POS Opening Shift"):
        return None
    if not frappe.db.exists("POS Opening Shift", shift_name):
        return None

    candidate_fields = [
        "pos_profile",
        "user",
        "period_start_date",
        "period_end_date",
        "status",
        "posting_date",
    ]
    fields = [field for field in candidate_fields if _field_exists("POS Opening Shift", field)]
    data = frappe.db.get_value("POS Opening Shift", shift_name, fields, as_dict=True) or frappe._dict()
    data["name"] = shift_name

    start_value = data.get("period_start_date") or data.get("posting_date")
    end_value = data.get("period_end_date")
    data["business_date"] = str(getdate(start_value)) if start_value else None
    data["start_date"] = str(getdate(start_value)) if start_value else None
    data["end_date"] = str(getdate(end_value)) if end_value else None
    return data


def business_date_for_shift(shift_name, fallback=None):
    info = get_shift_info(shift_name)
    if info and info.get("business_date"):
        return info.get("business_date")
    if fallback:
        return str(getdate(fallback))
    return nowdate()


def apply_session_shift_context(session, pos_opening_shift=None, draft_payload=None):
    shift_name = pos_opening_shift or shift_from_payload(draft_payload or getattr(session, "draft_payload", None))
    changed = False

    if hasattr(session, "pos_opening_shift") and shift_name and session.pos_opening_shift != shift_name:
        session.pos_opening_shift = shift_name
        changed = True

    if hasattr(session, "business_date") and not session.business_date:
        session.business_date = business_date_for_shift(shift_name, getattr(session, "creation", None))
        changed = True

    return changed


def _latest_shift_from_doctype(pos_profile=None, user=None, exclude=None):
    if not _doctype_exists("POS Opening Shift"):
        return None

    filters = {}
    if pos_profile and _field_exists("POS Opening Shift", "pos_profile"):
        filters["pos_profile"] = pos_profile
    if user and _field_exists("POS Opening Shift", "user"):
        filters["user"] = user
    if exclude:
        filters["name"] = ["!=", exclude]

    fields = ["name"]
    for field in ("pos_profile", "user", "period_start_date", "period_end_date", "status", "posting_date"):
        if _field_exists("POS Opening Shift", field):
            fields.append(field)
    order_by = "period_start_date desc" if _field_exists("POS Opening Shift", "period_start_date") else "creation desc"
    rows = frappe.get_all("POS Opening Shift", filters=filters, fields=fields, order_by=order_by, limit=1)
    if not rows:
        return None
    return get_shift_info(rows[0].name)


def _latest_shift_from_sessions(pos_profile=None, user=None, exclude=None):
    filters = {"pos_opening_shift": ["is", "set"]}
    if pos_profile:
        filters["pos_profile"] = pos_profile
    if user:
        filters["owner"] = user
    if exclude:
        filters["pos_opening_shift"] = ["not in", [exclude, ""]]
    rows = frappe.get_all(
        "POS Payment Session",
        filters=filters,
        fields=["pos_opening_shift", "business_date"],
        order_by="creation desc",
        limit=1,
    )
    if not rows:
        return None
    info = get_shift_info(rows[0].pos_opening_shift) or frappe._dict(
        name=rows[0].pos_opening_shift,
        pos_profile=pos_profile,
        user=user,
        business_date=str(rows[0].business_date) if rows[0].business_date else None,
    )
    return info


def get_last_shift(pos_profile=None, user=None, exclude=None):
    return _latest_shift_from_doctype(pos_profile=pos_profile, user=user, exclude=exclude) or _latest_shift_from_sessions(
        pos_profile=pos_profile, user=user, exclude=exclude
    )


def effective_pos_profile(requested=None, current_pos_profile=None):
    if can_view_all_profiles():
        return requested or None
    if current_pos_profile:
        if requested and requested != current_pos_profile:
            frappe.throw("You can only view Payment Hub transactions for your current POS Profile.", frappe.PermissionError)
        return current_pos_profile
    return requested or None


def validate_shift_scope(pos_opening_shift=None, pos_profile=None):
    if not pos_opening_shift:
        return
    info = get_shift_info(pos_opening_shift)
    if not info:
        return
    if pos_profile and info.get("pos_profile") and info.get("pos_profile") != pos_profile:
        frappe.throw("The selected POS shift does not belong to the selected POS Profile.", frappe.PermissionError)
    if not can_view_all_profiles() and info.get("user") and info.get("user") != frappe.session.user:
        frappe.throw("You can only view your own completed POS shift history.", frappe.PermissionError)


def profile_options():
    if not can_view_all_profiles():
        return []
    filters = {}
    if _field_exists("POS Profile", "disabled"):
        filters["disabled"] = 0
    return frappe.get_all("POS Profile", filters=filters, pluck="name", order_by="name asc")


def build_scope_context(current_pos_profile=None, current_pos_opening_shift=None, selected_pos_profile=None):
    privileged = can_view_all_profiles()
    current_shift = get_shift_info(current_pos_opening_shift)
    all_profiles_selected = bool(privileged and selected_pos_profile == "__all__")

    if privileged:
        if all_profiles_selected:
            selected_profile = None
        elif selected_pos_profile is not None:
            selected_profile = selected_pos_profile
        else:
            selected_profile = current_pos_profile
    else:
        selected_profile = effective_pos_profile(selected_pos_profile or current_pos_profile, current_pos_profile)

    if all_profiles_selected:
        # There is no meaningful single current/last shift when the manager is
        # viewing multiple profiles. Use a date scope instead.
        current_shift_for_scope = None
    elif current_shift and selected_profile and current_shift.get("pos_profile") != selected_profile:
        current_shift_for_scope = None
    else:
        current_shift_for_scope = current_shift

    last_user = None if privileged else frappe.session.user
    last_shift = get_last_shift(
        pos_profile=selected_profile,
        user=last_user,
        exclude=current_shift_for_scope.get("name") if current_shift_for_scope else None,
    ) if selected_profile else None

    if current_shift_for_scope:
        default_scope = "Current Shift"
        default_shift = current_shift_for_scope
    elif last_shift:
        default_scope = "Last Shift"
        default_shift = last_shift
    else:
        default_scope = "Today"
        default_shift = None

    today = nowdate()
    yesterday = str(add_days(today, -1))
    return {
        "user": frappe.session.user,
        "can_view_all_profiles": privileged,
        "current_pos_profile": current_pos_profile,
        "selected_pos_profile": "__all__" if all_profiles_selected else selected_profile,
        "profiles": profile_options(),
        "current_shift": current_shift_for_scope,
        "last_shift": last_shift,
        "default_scope": default_scope,
        "default_shift": default_shift,
        "today": today,
        "yesterday": yesterday,
    }


def backfill_session_shift_context():
    if not frappe.db.has_column("POS Payment Session", "pos_opening_shift"):
        return

    rows = frappe.get_all(
        "POS Payment Session",
        filters={"pos_opening_shift": ["is", "not set"]},
        fields=["name", "invoice_doctype", "invoice_name", "draft_payload", "creation"],
        limit=10000,
    )
    for row in rows:
        shift_name = shift_from_payload(row.draft_payload)
        if not shift_name and row.invoice_name and row.invoice_doctype and frappe.db.exists(row.invoice_doctype, row.invoice_name):
            if frappe.get_meta(row.invoice_doctype).has_field("posa_pos_opening_shift"):
                shift_name = frappe.db.get_value(row.invoice_doctype, row.invoice_name, "posa_pos_opening_shift")
        values = {}
        if shift_name:
            values["pos_opening_shift"] = shift_name
        if frappe.db.has_column("POS Payment Session", "business_date"):
            values["business_date"] = business_date_for_shift(shift_name, row.creation)
        if values:
            frappe.db.set_value("POS Payment Session", row.name, values, update_modified=False)

    if frappe.db.has_column("POS Payment Session", "business_date"):
        blank_dates = frappe.get_all(
            "POS Payment Session",
            filters={"business_date": ["is", "not set"]},
            fields=["name", "creation"],
            limit=10000,
        )
        for row in blank_dates:
            frappe.db.set_value(
                "POS Payment Session", row.name, "business_date", str(getdate(row.creation)), update_modified=False
            )

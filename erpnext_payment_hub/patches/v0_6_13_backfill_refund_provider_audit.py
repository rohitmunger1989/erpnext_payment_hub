import frappe


def execute():
    if not frappe.db.exists("DocType", "POS Refund Allocation"):
        return

    from erpnext_payment_hub.pos.refund import _sync_refund_provider_audit_fields

    names = frappe.get_all(
        "POS Refund Allocation",
        filters={"refund_gateway_transaction": ["is", "set"]},
        pluck="name",
    )
    for name in names:
        try:
            row = frappe.get_doc("POS Refund Allocation", name)
            _sync_refund_provider_audit_fields(row)
            row.save(ignore_permissions=True)
        except Exception:
            frappe.log_error(
                title=f"Payment Hub refund audit backfill failed: {name}",
                message=frappe.get_traceback(),
            )

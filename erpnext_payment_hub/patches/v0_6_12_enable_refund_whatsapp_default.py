import frappe


def execute():
    """Enable refund WhatsApp confirmation on upgraded sites by default."""
    settings = frappe.get_single("Payment Hub Settings")
    if getattr(settings, "send_refund_whatsapp", None) in (None, 0, "0", ""):
        settings.send_refund_whatsapp = 1
        settings.save(ignore_permissions=True)

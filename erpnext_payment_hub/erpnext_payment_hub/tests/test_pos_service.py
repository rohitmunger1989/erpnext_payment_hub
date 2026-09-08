from erpnext_payment_hub.pos.service import PAID_PENDING_SESSION_STATUSES, PENDING_SESSION_STATUSES


def test_pos_status_sets():
    assert "Payment Pending" in PENDING_SESSION_STATUSES
    assert "Ready to Complete" in PAID_PENDING_SESSION_STATUSES

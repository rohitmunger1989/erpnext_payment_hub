from erpnext_payment_hub.pos.service import FAILED_SESSION_STATUSES, PAID_PENDING_SESSION_STATUSES, PENDING_SESSION_STATUSES


def test_pos_status_sets():
    assert "Payment Pending" in PENDING_SESSION_STATUSES
    assert "Ready to Complete" in PAID_PENDING_SESSION_STATUSES


def test_failed_status_sets():
    assert "Failed" in FAILED_SESSION_STATUSES
    assert "Expired" in FAILED_SESSION_STATUSES
    assert "Cancelled" in FAILED_SESSION_STATUSES

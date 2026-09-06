import hoststorm.recovery_retry as recovery_retry


def test_retry_backoff_progression_and_cap():
    assert recovery_retry._retry_delay(0) == 5
    assert recovery_retry._retry_delay(1) == 15
    assert recovery_retry._retry_delay(2) == 30
    assert recovery_retry._retry_delay(3) == 60
    assert recovery_retry._retry_delay(99) == 60

"""
Unit tests for the abuse-detection module (app/security_monitor.py).
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import security_monitor


def _reset(client_key):
    security_monitor._failures.pop(client_key, None)
    security_monitor._blocked_until.pop(client_key, None)


def test_client_not_blocked_by_default():
    _reset("client-fresh")
    assert security_monitor.is_blocked("client-fresh") is False


def test_client_blocked_after_threshold_failures():
    client = "client-bruteforce"
    _reset(client)

    for _ in range(security_monitor.FAILURE_THRESHOLD - 1):
        blocked_now = security_monitor.record_auth_failure(client, "/auth/login")
        assert blocked_now is False
        assert security_monitor.is_blocked(client) is False

    # The threshold-crossing call should flag the block.
    blocked_now = security_monitor.record_auth_failure(client, "/auth/login")
    assert blocked_now is True
    assert security_monitor.is_blocked(client) is True


def test_alert_is_logged_when_client_is_blocked():
    client = "client-logged"
    _reset(client)
    before_count = len(security_monitor.alerts)

    for _ in range(security_monitor.FAILURE_THRESHOLD):
        security_monitor.record_auth_failure(client, "/auth/login")

    assert len(security_monitor.alerts) == before_count + 1
    assert security_monitor.alerts[-1]["client"] == client
    assert security_monitor.alerts[-1]["reason"] == "repeated_auth_failures"


def test_old_failures_outside_window_do_not_count():
    client = "client-old-failures"
    _reset(client)

    # Manually seed failures that are already outside the tracking window.
    old_time = time.monotonic() - security_monitor.FAILURE_WINDOW_SECONDS - 10
    security_monitor._failures[client].extend([old_time] * (security_monitor.FAILURE_THRESHOLD - 1))

    # One fresh failure shouldn't push it over the threshold, since the old
    # ones should be pruned as expired.
    blocked_now = security_monitor.record_auth_failure(client, "/auth/login")
    assert blocked_now is False
    assert security_monitor.is_blocked(client) is False


def test_different_clients_tracked_independently():
    _reset("client-a")
    _reset("client-b")

    for _ in range(security_monitor.FAILURE_THRESHOLD):
        security_monitor.record_auth_failure("client-a", "/auth/login")

    assert security_monitor.is_blocked("client-a") is True
    assert security_monitor.is_blocked("client-b") is False

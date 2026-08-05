"""
Unit tests for the gateway's token-bucket rate limiter (app/main.py::_allow_request).
Pure logic, no network -- fast and deterministic except where we deliberately
sleep to test refill behavior.
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import _allow_request, _buckets, RATE_LIMIT_CAPACITY


def test_allows_requests_up_to_burst_capacity():
    client_key = "test-client-burst"
    _buckets.pop(client_key, None)

    results = [_allow_request(client_key) for _ in range(RATE_LIMIT_CAPACITY)]
    assert all(results), "all requests within burst capacity should be allowed"


def test_blocks_requests_once_capacity_is_exhausted():
    client_key = "test-client-exhausted"
    _buckets.pop(client_key, None)

    for _ in range(RATE_LIMIT_CAPACITY):
        _allow_request(client_key)

    # one more immediately after exhausting the bucket should be rejected
    assert _allow_request(client_key) is False


def test_bucket_refills_over_time():
    client_key = "test-client-refill"
    _buckets.pop(client_key, None)

    for _ in range(RATE_LIMIT_CAPACITY):
        _allow_request(client_key)
    assert _allow_request(client_key) is False

    # refill rate is 5 tokens/sec -- waiting slightly over 0.2s should grant
    # at least one more token back
    time.sleep(0.25)
    assert _allow_request(client_key) is True


def test_different_clients_have_independent_buckets():
    _buckets.pop("client-a", None)
    _buckets.pop("client-b", None)

    for _ in range(RATE_LIMIT_CAPACITY):
        _allow_request("client-a")
    assert _allow_request("client-a") is False

    # client-b's bucket is untouched, so it should still be allowed
    assert _allow_request("client-b") is True

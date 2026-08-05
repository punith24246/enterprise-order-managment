"""
Lightweight abuse/anomaly detection at the gateway.

This is intentionally scoped small: it detects one concrete abuse pattern
(repeated authentication failures from the same client -- the signature of
credential stuffing / brute-force login attempts) and auto-blocks the client
for a cooldown period once a threshold is crossed. It's not a general-purpose
WAF or ML-based anomaly detector -- it's the kind of first concrete layer
you'd actually build before reaching for something heavier.

Same in-memory + lock pattern as the rate limiter (see main.py) and the same
caveat: this state is per-gateway-instance. At real scale, this would move to
a shared store (Redis) so all gateway replicas see the same picture of a
given client's behavior, and likely feed into a proper SIEM/alerting pipeline
rather than just an in-memory list.
"""
import threading
import time
from collections import deque, defaultdict

FAILURE_WINDOW_SECONDS = 60      # how far back we look
FAILURE_THRESHOLD = 5            # failures within the window that triggers a block
BLOCK_DURATION_SECONDS = 120     # how long a client stays blocked once flagged

_failures: dict[str, deque] = defaultdict(deque)
_blocked_until: dict[str, float] = {}
_lock = threading.Lock()

# Rolling history of flagged events, for the /security/alerts endpoint.
alerts: list[dict] = []


def is_blocked(client_key: str) -> bool:
    with _lock:
        blocked_until = _blocked_until.get(client_key)
        if blocked_until is None:
            return False
        if time.monotonic() >= blocked_until:
            # Cooldown expired -- unblock.
            del _blocked_until[client_key]
            return False
        return True


def record_auth_failure(client_key: str, path: str) -> bool:
    """Records a 401/403 for this client. Returns True if this call caused the
    client to newly cross the threshold and get blocked."""
    now = time.monotonic()
    with _lock:
        window = _failures[client_key]
        window.append(now)
        while window and now - window[0] > FAILURE_WINDOW_SECONDS:
            window.popleft()

        if len(window) >= FAILURE_THRESHOLD and client_key not in _blocked_until:
            _blocked_until[client_key] = now + BLOCK_DURATION_SECONDS
            alerts.append({
                "client": client_key,
                "reason": "repeated_auth_failures",
                "failure_count": len(window),
                "path": path,
                "blocked_for_seconds": BLOCK_DURATION_SECONDS,
                "timestamp": time.time(),
            })
            return True
    return False

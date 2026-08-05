"""Track repeated auth failures and temporarily block noisy clients."""

import threading
import time
from collections import defaultdict, deque

FAILURE_WINDOW_SECONDS = 60
FAILURE_THRESHOLD = 5
BLOCK_DURATION_SECONDS = 120

_failures: dict[str, deque] = defaultdict(deque)
_blocked_until: dict[str, float] = {}
_lock = threading.Lock()

alerts: list[dict] = []


def is_blocked(client_key: str) -> bool:
    with _lock:
        blocked_until = _blocked_until.get(client_key)
        if blocked_until is None:
            return False
        if time.monotonic() >= blocked_until:
            del _blocked_until[client_key]
            return False
        return True


def record_auth_failure(client_key: str, path: str) -> bool:
    """Record a failed auth attempt and return True when it creates a block."""
    now = time.monotonic()
    with _lock:
        window = _failures[client_key]
        window.append(now)
        while window and now - window[0] > FAILURE_WINDOW_SECONDS:
            window.popleft()

        if len(window) >= FAILURE_THRESHOLD and client_key not in _blocked_until:
            _blocked_until[client_key] = now + BLOCK_DURATION_SECONDS
            alerts.append(
                {
                    "client": client_key,
                    "reason": "repeated_auth_failures",
                    "failure_count": len(window),
                    "path": path,
                    "blocked_for_seconds": BLOCK_DURATION_SECONDS,
                    "timestamp": time.time(),
                }
            )
            return True
    return False

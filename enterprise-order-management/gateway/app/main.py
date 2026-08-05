"""
API Gateway — single entry point for clients.

Responsibilities:
  - Routes requests to the right downstream service based on path prefix.
  - Validates JWTs centrally for protected routes so individual services don't
    each need to own "is this a route the public can hit" logic — though each
    service ALSO independently verifies the token signature (see deps.py in
    inventory/order services), since trusting the gateway blindly would mean a
    compromised gateway = compromised everything. Defense in depth.
  - Forwards the Authorization header through untouched so downstream services
    can re-verify and extract user/role info themselves.

Not implemented here (would be next steps in a real system): rate limiting,
request/response logging & tracing (correlation IDs), circuit breaking on
downstream failures, response caching.
"""
import os
import time
import uuid
import logging
import threading
from collections import defaultdict

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from jose import jwt, JWTError

from . import security_monitor

JWT_SECRET = os.getenv("JWT_SECRET", "supersecretkey_change_me")
JWT_ALGORITHM = "HS256"

AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://localhost:8001")
INVENTORY_SERVICE_URL = os.getenv("INVENTORY_SERVICE_URL", "http://localhost:8002")
ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://localhost:8003")

# Routes that don't require a valid JWT to pass through.
PUBLIC_PATHS = {"/auth/register", "/auth/login", "/health"}

ROUTES = {
    "/auth": AUTH_SERVICE_URL,
    "/products": INVENTORY_SERVICE_URL,
    "/orders": ORDER_SERVICE_URL,
}

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("gateway")

app = FastAPI(title="API Gateway")


# ---------------------------------------------------------------------------
# Rate limiting -- token bucket per client, keyed by IP (or by authenticated
# user id, once known). In-memory + a lock is fine for a single gateway
# instance; if the gateway is ever scaled horizontally this state would need
# to move to Redis so all instances share the same bucket per client.
# ---------------------------------------------------------------------------
RATE_LIMIT_CAPACITY = 20       # max burst size
RATE_LIMIT_REFILL_PER_SEC = 5  # steady-state requests/sec allowed

_buckets: dict[str, dict] = defaultdict(lambda: {"tokens": RATE_LIMIT_CAPACITY, "last_refill": time.monotonic()})
_buckets_lock = threading.Lock()


def _allow_request(client_key: str) -> bool:
    with _buckets_lock:
        bucket = _buckets[client_key]
        now = time.monotonic()
        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(RATE_LIMIT_CAPACITY, bucket["tokens"] + elapsed * RATE_LIMIT_REFILL_PER_SEC)
        bucket["last_refill"] = now

        if bucket["tokens"] >= 1:
            bucket["tokens"] -= 1
            return True
        return False


@app.get("/health")
def health():
    return {"status": "ok", "service": "gateway"}


@app.get("/security/alerts")
def security_alerts(request: Request):
    """Admin-only view of clients that have been auto-blocked for suspicious
    auth behavior (repeated login/token failures)."""
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = auth_header.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if payload.get("role") != "ADMIN":
        raise HTTPException(status_code=403, detail="Admin role required")

    return {"alerts": security_monitor.alerts}


def resolve_upstream(path: str) -> str | None:
    for prefix, base_url in ROUTES.items():
        if path.startswith(prefix):
            return base_url
    return None


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(full_path: str, request: Request):
    start = time.monotonic()
    path = f"/{full_path}"

    # Correlation ID: reuse one if the caller already supplied it (useful for
    # chained calls / tracing across a wider system), otherwise mint one. This
    # gets forwarded downstream and logged at every hop, so a single request
    # can be traced across all four services from one ID.
    correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))

    upstream = resolve_upstream(path)
    if upstream is None:
        raise HTTPException(status_code=404, detail="No route matches this path")

    client_key = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")

    if security_monitor.is_blocked(client_key):
        logger.info(f"[{correlation_id}] 403 blocked_client client={client_key} path={path}")
        raise HTTPException(status_code=403, detail="Blocked due to suspicious activity, try again later")

    if not _allow_request(client_key):
        logger.info(f"[{correlation_id}] 429 rate_limited client={client_key} path={path}")
        raise HTTPException(status_code=429, detail="Rate limit exceeded, slow down")

    if path not in PUBLIC_PATHS:
        auth_header = request.headers.get("authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            security_monitor.record_auth_failure(client_key, path)
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        token = auth_header.split(" ", 1)[1]
        try:
            jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except JWTError:
            security_monitor.record_auth_failure(client_key, path)
            raise HTTPException(status_code=401, detail="Invalid or expired token")

    body = await request.body()
    forward_headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    forward_headers["x-correlation-id"] = correlation_id

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            upstream_response = await client.request(
                method=request.method,
                url=f"{upstream}{path}",
                params=request.query_params,
                headers=forward_headers,
                content=body,
            )
        except httpx.ConnectError:
            logger.info(f"[{correlation_id}] 503 upstream_unavailable path={path} upstream={upstream}")
            raise HTTPException(status_code=503, detail=f"Upstream service unavailable: {upstream}")

    duration_ms = round((time.monotonic() - start) * 1000, 1)
    logger.info(
        f"[{correlation_id}] {request.method} {path} -> {upstream_response.status_code} ({duration_ms}ms)"
    )

    if upstream_response.status_code in (401, 403) and path == "/auth/login":
        # Failed login attempts are the clearest brute-force / credential-
        # stuffing signal -- worth tracking even though /auth/login is a
        # public path that skips the gateway's own JWT check above.
        newly_blocked = security_monitor.record_auth_failure(client_key, path)
        if newly_blocked:
            logger.info(f"[{correlation_id}] client {client_key} auto-blocked after repeated failed logins")

    response = JSONResponse(
        status_code=upstream_response.status_code,
        content=upstream_response.json() if upstream_response.content else None,
    )
    response.headers["x-correlation-id"] = correlation_id
    return response

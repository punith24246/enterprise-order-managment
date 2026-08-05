import logging
import time
from starlette.middleware.base import BaseHTTPMiddleware

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


class CorrelationLoggingMiddleware(BaseHTTPMiddleware):
    """Logs the correlation ID forwarded by the gateway alongside method, path,
    status, and latency -- so a single request can be traced across services
    by grepping logs for one ID, instead of guessing which service call
    corresponds to which incoming request."""

    async def dispatch(self, request, call_next):
        correlation_id = request.headers.get("x-correlation-id", "none")
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000, 1)
        logger.info(
            f"[{correlation_id}] {request.method} {request.url.path} -> {response.status_code} ({duration_ms}ms)"
        )
        response.headers["x-correlation-id"] = correlation_id
        return response

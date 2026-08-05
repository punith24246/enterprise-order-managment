# Enterprise Order Management — Microservices Backend

A small e-commerce-style backend split into four independently deployable services,
built to practice (and demonstrate) core backend/distributed-systems concepts:
service boundaries, API gateway routing, JWT auth, and failure handling across
service calls.

## Services

| Service    | Port | Responsibility                                  |
|------------|------|--------------------------------------------------|
| gateway    | 8000 | Single entry point, routes requests, validates JWTs |
| auth       | 8001 | User registration/login, JWT issuance             |
| inventory  | 8002 | Product catalog, stock levels                     |
| order      | 8003 | Order creation (orchestrates the saga below)       |

All clients talk to the gateway (`:8000`) only. The other ports are exposed for
local debugging but wouldn't be public in a real deployment.

## Running it

```bash
docker compose up --build
```

Then:
```bash
# Register + login
curl -X POST localhost:8000/auth/register -H "Content-Type: application/json" \
  -d '{"email":"admin@test.com","password":"pass123","role":"ADMIN"}'

curl -X POST localhost:8000/auth/login -H "Content-Type: application/json" \
  -d '{"email":"admin@test.com","password":"pass123"}'
# -> copy the access_token from the response

# Create a product (admin only)
curl -X POST localhost:8000/products -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"Wireless Mouse","sku":"WM-001","price":19.99,"stock_quantity":50}'

# Place an order
curl -X POST localhost:8000/orders -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"items":[{"product_id":1,"quantity":2}]}'
# Re-sending the exact same request (same Idempotency-Key) returns the
# original order instead of creating a duplicate.
```

## Running the tests

Each service has its own isolated test suite (in-memory SQLite / mocked HTTP
calls, no need for the real Postgres or a running docker-compose stack):

```bash
cd order-service && pip install -r requirements.txt && pytest tests/ -v
cd inventory-service && pip install -r requirements.txt && pytest tests/ -v
cd gateway && pip install -r requirements.txt && pytest tests/ -v
```

9 tests total — 2 saga tests, 3 inventory stock tests, 4 rate-limiter tests.

## Design decisions worth knowing (and being ready to defend)

**Why microservices instead of one monolith?**
Each service owns its own data and can be scaled, deployed, and modified
independently — e.g. Order volume typically dwarfs Product-catalog write
volume, so they have very different scaling needs. The tradeoff is added
operational complexity (network calls instead of function calls, no shared
transactions) — which is exactly what the next two points address.

**Why a saga instead of a two-phase commit for order creation?**
Order-service and Inventory-service each own their own database. A 2PC would
require both to block on a shared coordinator and hold locks across a network
call, which doesn't scale and creates a single point of failure. Instead,
`order-service/app/saga.py` orchestrates: create order (PENDING) → reserve
stock in inventory-service → confirm order, OR on any failure, run
compensating actions (release any stock already reserved) and mark the order
FAILED. This trades strict atomicity for availability + eventual consistency —
acceptable for order processing, not acceptable for something like a ledger.

**Why does each service independently verify the JWT instead of trusting the gateway?**
Defense in depth. If the gateway is misconfigured or compromised, a service
that blindly trusts "the gateway already checked this" has no protection left.
Each service holds the same signing secret and re-validates. The cost is
duplicated logic (`deps.py` in each service) — an acceptable tradeoff at this
scale; at larger scale this would move to a shared auth library or a service
mesh handling mTLS + authz centrally.

## What's deliberately left out (and why), given more time:
- Distributed tracing dashboards (Jaeger/Zipkin) — correlation IDs exist and are
  logged, but there's no UI to visualize traces yet, just grep-able logs.
- Retry-with-backoff on the inventory-service calls before falling back to
  the compensating-action path (currently fails fast on the first error).
- Each service uses the same Postgres instance with separate tables (not
  separate DB instances) to keep local dev light — in production these would
  be fully separate databases to enforce the "no shared schema" boundary.
- Rate limiter state is in-memory on the gateway — fine for one instance, but
  would need to move to Redis if the gateway were ever scaled horizontally,
  since each replica would otherwise track its own separate bucket per client.

## Production-hardening upgrades

The base version above covers the core architecture. These five were added
afterward specifically to close gaps a stronger interviewer would probe on:

**1. Concurrency-safe stock (row-level locking)**
`inventory-service`'s `adjust-stock` endpoint now takes a `SELECT ... FOR
UPDATE` lock before reading `stock_quantity`. Without it, two concurrent
requests for the same product could both read stock=5, both independently
decide "there's enough," and both commit — a lost-update race that lets you
oversell inventory. The lock forces the second concurrent transaction to wait
until the first commits, so it reads the post-update quantity. (This is a
Postgres-level guarantee; the SQLite-backed unit tests in `tests/test_stock.py`
verify the endpoint's correctness logic but don't exercise real concurrent
locking — that needs the real Postgres instance from docker-compose.)

**2. Idempotency keys on order creation**
`POST /orders` now accepts an optional `Idempotency-Key` header. If a request
with the same key arrives again — a client retry after a timeout, a
double-click, at-least-once delivery from a queue in front of this API — the
original order is returned instead of creating (and double-reserving stock
for) a duplicate. There's also a race-safe path: if two requests with the same
key somehow both pass the initial check before either commits, the database's
unique constraint on `idempotency_key` catches the second insert, which is
handled gracefully rather than crashing.

**3. Test suite**
9 tests total: 2 for the saga (happy path + the failure/compensation path,
using a mocked `httpx.Client` so no real inventory-service is needed), 3 for
inventory stock adjustment logic (in-memory SQLite + FastAPI's
`dependency_overrides` to stub auth), and 4 for the gateway's rate limiter
(burst allowance, exhaustion, refill over time, per-client isolation). Run
with `pytest` inside each service's directory.

**4. Rate limiting at the gateway**
A token-bucket limiter (20 request burst, 5 requests/sec steady-state refill)
keyed per client IP, implemented as plain in-memory state behind a lock —
intentionally simple rather than pulling in Redis for a single-gateway-instance
setup. Returns `429` once a client's bucket is empty.

**5. Correlation IDs across all four services**
The gateway mints a `x-correlation-id` (or reuses one if the caller already
sent one) and forwards it downstream on every proxied call. Each service logs
it via a shared `CorrelationLoggingMiddleware`, and it's threaded all the way
into the order saga's own outbound calls to inventory-service — so a single
request, including its saga's reservation and compensation calls, can be
traced end-to-end by grepping logs for one ID across all four services.

## Tech stack
FastAPI, SQLAlchemy, Postgres, JWT (python-jose), Docker Compose, pytest.

## Optional: abuse detection at the gateway

`gateway/app/security_monitor.py` adds one concrete abuse-detection pattern on
top of the base gateway: repeated authentication failures from the same
client (the signature of credential-stuffing / brute-force login attempts)
are tracked in a sliding window, and a client that crosses the threshold
(5 failures within 60 seconds, by default) is auto-blocked for a cooldown
period (120 seconds). This covers both:
- Requests rejected by the gateway's own JWT check (missing/invalid token)
- Failed logins at `/auth/login` itself (the clearer signal, since that's
  where an actual credential-stuffing attempt would show up)

Blocked/flagged clients are visible via `GET /security/alerts` (admin JWT
required). This is intentionally scoped small -- one concrete pattern, not a
general-purpose WAF or ML-based detector -- but it's a real example of the
kind of first layer you'd build before reaching for something heavier.

This module is optional/separable from the core project and exists
specifically to demonstrate applied security thinking on top of the base
backend architecture. 5 tests cover it (`gateway/tests/test_security_monitor.py`):
threshold crossing, alert logging, sliding-window expiry, and per-client
isolation.

# Enterprise Order Management

Small FastAPI microservices project for an order flow: users authenticate,
admins manage products, and customers place orders through a gateway.

The system is intentionally compact, but it includes a few things that matter
once services talk over HTTP: JWT validation, request correlation IDs,
idempotent order creation, stock reservation with compensation, and gateway
rate limiting.

## Services

| Service | Port | Role |
| --- | ---: | --- |
| gateway | 8000 | Public entry point, routing, JWT checks, rate limiting |
| auth-service | 8001 | Registration, login, JWT issuing |
| inventory-service | 8002 | Products and stock updates |
| order-service | 8003 | Order creation and stock-reservation saga |

Clients should call the gateway on port `8000`. The other ports are exposed by
Docker Compose for local debugging.

## Run Locally

```bash
docker compose up --build
```

Register and log in:

```bash
curl -X POST localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@test.com","password":"pass123","role":"ADMIN"}'

curl -X POST localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@test.com","password":"pass123"}'
```

Create a product:

```bash
curl -X POST localhost:8000/products \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"Wireless Mouse","sku":"WM-001","price":19.99,"stock_quantity":50}'
```

Place an order:

```bash
curl -X POST localhost:8000/orders \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: <unique-request-id>" \
  -d '{"items":[{"product_id":1,"quantity":2}]}'
```

Reusing the same `Idempotency-Key` returns the original order instead of
creating a duplicate.

## Tests

Each service has its own test suite. The tests use in-memory SQLite or mocked
HTTP clients, so the Docker Compose stack does not need to be running.

```bash
cd order-service && pip install -r requirements.txt && pytest tests/ -v
cd inventory-service && pip install -r requirements.txt && pytest tests/ -v
cd gateway && pip install -r requirements.txt && pytest tests/ -v
```

Current coverage is focused on the order saga, stock adjustment, gateway rate
limiting, and the simple abuse-detection module.

## How Order Creation Works

`order-service` creates the order in `PENDING` state, then asks
`inventory-service` to reserve stock for each item. If every reservation
succeeds, the order becomes `CONFIRMED`.

If a reservation fails partway through, the order service releases any stock it
already reserved and marks the order `FAILED`. That compensation step is the
main reason the order flow is implemented as a saga instead of trying to make
one database transaction span multiple services.

The inventory update uses `SELECT ... FOR UPDATE` before changing
`stock_quantity`, so concurrent requests for the same product are serialized by
Postgres instead of both reading the same stale value.

## Gateway Behavior

The gateway handles shared concerns before forwarding requests:

- validates JWTs on protected routes
- forwards the original `Authorization` header to downstream services
- adds or preserves an `x-correlation-id`
- applies a token-bucket rate limit per client IP
- tracks repeated auth failures and temporarily blocks noisy clients

Downstream services still verify JWTs themselves. That keeps the services from
depending entirely on the gateway for authorization if a route is accidentally
exposed.

Admin users can inspect blocked clients at:

```text
GET /security/alerts
```

## Notes for Production

This repo is set up for local development, not as a drop-in production
deployment.

- Replace the default `JWT_SECRET` values before deploying.
- Replace `INTERNAL_SERVICE_TOKEN`; Docker Compose uses a local development
  value so `order-service` can call inventory stock updates.
- Keep `auth-service`, `inventory-service`, and `order-service` private behind
  the gateway.
- Move gateway rate-limit and abuse-detection state to Redis or another shared
  store before running multiple gateway replicas.
- Give each service its own database if you want a stronger service boundary.
  Docker Compose uses one Postgres container to keep local setup simple.
- Use a secret manager for service tokens and database credentials.
- Failed compensation currently needs operational follow-up. A real deployment
  should retry it with a job queue or record it for reconciliation.

## Tech Stack

FastAPI, SQLAlchemy, Postgres, Docker Compose, JWT with `python-jose`, pytest.

"""
Tests for stock adjustment logic -- the endpoint the order saga depends on.
Note: the row-level lock (with_for_update) added for concurrency safety is a
Postgres-level guarantee and isn't meaningfully exercised by SQLite in these
unit tests; that behavior is validated by a manual concurrency test (see
README "Verifying the concurrency fix" section) against the real docker-compose
Postgres instance instead.
"""
def _make_product(client):
    # Auth is stubbed via app.dependency_overrides in conftest.py, so no real
    # JWT is needed here -- the Authorization header just needs to be present.
    resp = client.post(
        "/products",
        json={"name": "Widget", "sku": "W-1", "price": 9.99, "stock_quantity": 10},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201
    return resp.json()


def test_deduct_stock_within_available_quantity_succeeds(client):
    product = _make_product(client)

    resp = client.post(f"/products/{product['id']}/adjust-stock", json={"delta": -4})
    assert resp.status_code == 200
    assert resp.json()["stock_quantity"] == 6


def test_deduct_stock_beyond_available_quantity_returns_409(client):
    product = _make_product(client)

    resp = client.post(f"/products/{product['id']}/adjust-stock", json={"delta": -50})
    assert resp.status_code == 409

    # confirm stock was NOT modified on the failed attempt
    get_resp = client.get(f"/products/{product['id']}")
    assert get_resp.json()["stock_quantity"] == 10


def test_release_stock_restores_quantity(client):
    product = _make_product(client)
    client.post(f"/products/{product['id']}/adjust-stock", json={"delta": -4})

    resp = client.post(f"/products/{product['id']}/adjust-stock", json={"delta": 4})
    assert resp.status_code == 200
    assert resp.json()["stock_quantity"] == 10

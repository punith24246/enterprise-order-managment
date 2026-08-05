def _make_product(client):
    resp = client.post(
        "/products",
        json={"name": "Widget", "sku": "W-1", "price": 9.99, "stock_quantity": 10},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201
    return resp.json()


def test_deduct_stock_within_available_quantity_succeeds(client):
    product = _make_product(client)

    resp = client.post(
        f"/products/{product['id']}/adjust-stock",
        json={"delta": -4},
        headers={"X-Service-Token": "local-service-token"},
    )
    assert resp.status_code == 200
    assert resp.json()["stock_quantity"] == 6


def test_deduct_stock_beyond_available_quantity_returns_409(client):
    product = _make_product(client)

    resp = client.post(
        f"/products/{product['id']}/adjust-stock",
        json={"delta": -50},
        headers={"X-Service-Token": "local-service-token"},
    )
    assert resp.status_code == 409

    get_resp = client.get(f"/products/{product['id']}")
    assert get_resp.json()["stock_quantity"] == 10


def test_adjust_stock_requires_service_token(client):
    product = _make_product(client)

    resp = client.post(f"/products/{product['id']}/adjust-stock", json={"delta": -1})
    assert resp.status_code == 403


def test_release_stock_restores_quantity(client):
    product = _make_product(client)
    client.post(
        f"/products/{product['id']}/adjust-stock",
        json={"delta": -4},
        headers={"X-Service-Token": "local-service-token"},
    )

    resp = client.post(
        f"/products/{product['id']}/adjust-stock",
        json={"delta": 4},
        headers={"X-Service-Token": "local-service-token"},
    )
    assert resp.status_code == 200
    assert resp.json()["stock_quantity"] == 10

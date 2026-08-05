from unittest.mock import MagicMock, patch

from app import models, saga


class FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.content = b"{}"

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 500:
            raise Exception(f"upstream error {self.status_code}")


def make_fake_client(get_responses, post_responses):
    fake = MagicMock()
    fake.get.side_effect = get_responses
    fake.post.side_effect = post_responses
    fake.__enter__.return_value = fake
    fake.__exit__.return_value = False
    return fake


def make_order(db_session, user_id=1):
    order = models.Order(user_id=user_id, status=models.OrderStatus.PENDING)
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order


def test_saga_confirms_order_when_all_items_reserve_successfully(db_session):
    order = make_order(db_session)
    items_in = [{"product_id": 1, "quantity": 2}]

    fake_client = make_fake_client(
        get_responses=[FakeResponse(200, {"id": 1, "price": "10.00", "stock_quantity": 5})],
        post_responses=[FakeResponse(200, {"stock_quantity": 3})],
    )

    with patch("app.saga.httpx.Client", return_value=fake_client):
        result = saga.run_order_saga(db_session, order, items_in)

    assert result.status == models.OrderStatus.CONFIRMED
    assert float(result.total_amount) == 20.0
    assert len(result.items) == 1


def test_saga_fails_and_releases_stock_when_second_item_has_insufficient_stock(db_session):
    order = make_order(db_session)
    items_in = [
        {"product_id": 1, "quantity": 2},
        {"product_id": 2, "quantity": 100},
    ]

    fake_client = make_fake_client(
        get_responses=[
            FakeResponse(200, {"id": 1, "price": "10.00", "stock_quantity": 5}),
            FakeResponse(200, {"id": 2, "price": "5.00", "stock_quantity": 3}),
        ],
        post_responses=[
            FakeResponse(200, {"stock_quantity": 3}),
            FakeResponse(409, {}),
            FakeResponse(200, {"stock_quantity": 5}),
        ],
    )

    with patch("app.saga.httpx.Client", return_value=fake_client):
        result = saga.run_order_saga(db_session, order, items_in)

    assert result.status == models.OrderStatus.FAILED
    assert "product 2" in result.failure_reason
    assert fake_client.post.call_count == 3

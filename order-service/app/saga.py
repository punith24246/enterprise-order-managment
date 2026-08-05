"""Order creation saga."""

import os

import httpx
from sqlalchemy.orm import Session

from . import models

INVENTORY_SERVICE_URL = os.getenv("INVENTORY_SERVICE_URL", "http://localhost:8002")
INTERNAL_SERVICE_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN", "local-service-token")


class SagaFailure(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _get_product(client: httpx.Client, product_id: int, headers: dict) -> dict:
    resp = client.get(f"{INVENTORY_SERVICE_URL}/products/{product_id}", headers=headers)
    if resp.status_code == 404:
        raise SagaFailure(f"Product {product_id} not found")
    resp.raise_for_status()
    return resp.json()


def _reserve_stock(client: httpx.Client, product_id: int, quantity: int, headers: dict) -> None:
    resp = client.post(
        f"{INVENTORY_SERVICE_URL}/products/{product_id}/adjust-stock",
        json={"delta": -quantity},
        headers=headers,
    )
    if resp.status_code == 409:
        raise SagaFailure(f"Insufficient stock for product {product_id}")
    resp.raise_for_status()


def _release_stock(client: httpx.Client, product_id: int, quantity: int, headers: dict) -> None:
    """Best-effort release for stock reserved before a later saga failure."""
    try:
        resp = client.post(
            f"{INVENTORY_SERVICE_URL}/products/{product_id}/adjust-stock",
            json={"delta": quantity},
            headers=headers,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        # This should be retried or reconciled by a background process in a
        # production system.
        pass


def _inventory_headers(correlation_id: str | None) -> dict:
    headers = {"x-service-token": INTERNAL_SERVICE_TOKEN}
    if correlation_id:
        headers["x-correlation-id"] = correlation_id
    return headers


def run_order_saga(
    db: Session,
    order: models.Order,
    items_in: list[dict],
    correlation_id: str | None = None,
) -> models.Order:
    reserved: list[tuple[int, int]] = []
    total_amount = 0.0
    headers = _inventory_headers(correlation_id)

    with httpx.Client(timeout=5.0) as client:
        try:
            for item in items_in:
                product = _get_product(client, item["product_id"], headers)
                _reserve_stock(client, item["product_id"], item["quantity"], headers)
                reserved.append((item["product_id"], item["quantity"]))

                unit_price = float(product["price"])
                total_amount += unit_price * item["quantity"]
                db.add(
                    models.OrderItem(
                        order_id=order.id,
                        product_id=item["product_id"],
                        quantity=item["quantity"],
                        unit_price=unit_price,
                    )
                )

        except (SagaFailure, httpx.HTTPError) as exc:
            for product_id, quantity in reversed(reserved):
                _release_stock(client, product_id, quantity, headers)

            order.status = models.OrderStatus.FAILED
            order.failure_reason = (
                str(exc) if isinstance(exc, SagaFailure) else "Inventory service unavailable"
            )
            db.commit()
            db.refresh(order)
            return order

    order.status = models.OrderStatus.CONFIRMED
    order.total_amount = total_amount
    db.commit()
    db.refresh(order)
    return order

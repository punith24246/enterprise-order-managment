"""
Order creation saga.

Why a saga instead of a distributed transaction (2PC)?
Order-service and Inventory-service each own their own data and don't share a
transaction manager. A two-phase commit would require both services to block on
a coordinator and hold locks across a network call — fragile, and it doesn't
scale well with more services. Instead we use an orchestrated saga:

  1. Create the Order locally in PENDING state (order-service's own transaction).
  2. For each item, call inventory-service to reserve stock (deduct quantity).
  3. If ANY reservation step fails (insufficient stock, network error, etc.),
     run compensating actions: release stock already reserved for the items that
     succeeded before the failure, then mark the Order FAILED.
  4. If all reservations succeed, mark the Order CONFIRMED.

This trades strict atomicity for availability + eventual consistency: there's a
brief window where stock is reserved in Inventory but the Order isn't confirmed
yet. That's an acceptable tradeoff for this domain (order processing) — it would
NOT be acceptable for something like a financial ledger, where you'd want a
different consistency model entirely.
"""
import os
import httpx
from sqlalchemy.orm import Session

from . import models

INVENTORY_SERVICE_URL = os.getenv("INVENTORY_SERVICE_URL", "http://localhost:8002")


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
    """Compensating action — best-effort restore of stock already reserved."""
    try:
        client.post(
            f"{INVENTORY_SERVICE_URL}/products/{product_id}/adjust-stock",
            json={"delta": quantity},
            headers=headers,
        )
    except httpx.HTTPError:
        # In production this would go to a dead-letter queue / retry job instead
        # of being silently swallowed, since a failed compensation means stock
        # is now inconsistent and needs manual or automated reconciliation.
        pass


def run_order_saga(
    db: Session, order: models.Order, items_in: list[dict], correlation_id: str | None = None
) -> models.Order:
    reserved: list[tuple[int, int]] = []  # (product_id, quantity) successfully reserved so far
    total_amount = 0.0
    headers = {"x-correlation-id": correlation_id} if correlation_id else {}

    with httpx.Client(timeout=5.0) as client:
        try:
            for item in items_in:
                product = _get_product(client, item["product_id"], headers)
                _reserve_stock(client, item["product_id"], item["quantity"], headers)
                reserved.append((item["product_id"], item["quantity"]))

                unit_price = float(product["price"])
                total_amount += unit_price * item["quantity"]
                db.add(models.OrderItem(
                    order_id=order.id,
                    product_id=item["product_id"],
                    quantity=item["quantity"],
                    unit_price=unit_price,
                ))

        except (SagaFailure, httpx.HTTPError) as exc:
            # Compensate everything reserved so far, in reverse order.
            for product_id, quantity in reversed(reserved):
                _release_stock(client, product_id, quantity, headers)

            order.status = models.OrderStatus.FAILED
            order.failure_reason = str(exc) if isinstance(exc, SagaFailure) else "Inventory service unavailable"
            db.commit()
            db.refresh(order)
            return order

    order.status = models.OrderStatus.CONFIRMED
    order.total_amount = total_amount
    db.commit()
    db.refresh(order)
    return order

from fastapi import FastAPI, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError

from . import models, schemas
from .database import engine, get_db, Base
from .deps import get_current_user
from .saga import run_order_saga
from .logging_middleware import CorrelationLoggingMiddleware

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Order Service", lifespan=lifespan)
app.add_middleware(CorrelationLoggingMiddleware)


@app.get("/health")
def health():
    return {"status": "ok", "service": "order-service"}


@app.post("/orders", response_model=schemas.OrderResponse, status_code=201)
def create_order(
    payload: schemas.OrderCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if not payload.items:
        raise HTTPException(status_code=400, detail="Order must contain at least one item")

    if idempotency_key:
        existing = (
            db.query(models.Order)
            .options(joinedload(models.Order.items))
            .filter(models.Order.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
            # Same key seen before -> return the original result instead of
            # re-running the saga (which would double-reserve stock).
            return existing

    order = models.Order(
        user_id=int(user["sub"]),
        status=models.OrderStatus.PENDING,
        idempotency_key=idempotency_key,
    )
    db.add(order)
    try:
        db.commit()
    except IntegrityError:
        # Two requests with the same key raced past the pre-check above and
        # both tried to insert -> the unique constraint on idempotency_key
        # catches it. Roll back and return the row the other request created.
        db.rollback()
        existing = (
            db.query(models.Order)
            .options(joinedload(models.Order.items))
            .filter(models.Order.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
            return existing
        raise
    db.refresh(order)

    items_in = [item.model_dump() for item in payload.items]
    correlation_id = request.headers.get("x-correlation-id")
    order = run_order_saga(db, order, items_in, correlation_id=correlation_id)

    if order.status == models.OrderStatus.FAILED:
        raise HTTPException(status_code=409, detail=order.failure_reason)

    return order


@app.get("/orders/{order_id}", response_model=schemas.OrderResponse)
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = (
        db.query(models.Order)
        .options(joinedload(models.Order.items))
        .filter(models.Order.id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@app.get("/orders", response_model=list[schemas.OrderResponse])
def list_orders(user_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(models.Order).options(joinedload(models.Order.items))
    if user_id is not None:
        query = query.filter(models.Order.user_id == user_id)
    return query.all()

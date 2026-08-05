from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from . import models, schemas
from .database import Base, engine, get_db
from .deps import get_current_user
from .logging_middleware import CorrelationLoggingMiddleware
from .saga import run_order_saga


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Order Service", lifespan=lifespan)
app.add_middleware(CorrelationLoggingMiddleware)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ok", "service": "order-service", "database": "ok"}


@app.post("/orders", response_model=schemas.OrderResponse, status_code=201)
def create_order(
    payload: schemas.OrderCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if idempotency_key:
        existing = (
            db.query(models.Order)
            .options(joinedload(models.Order.items))
            .filter(models.Order.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
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
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    order = (
        db.query(models.Order)
        .options(joinedload(models.Order.items))
        .filter(models.Order.id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if user.get("role") != "ADMIN" and order.user_id != int(user["sub"]):
        raise HTTPException(status_code=403, detail="Cannot view another user's order")

    return order


@app.get("/orders", response_model=list[schemas.OrderResponse])
def list_orders(
    user_id: int | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    query = db.query(models.Order).options(joinedload(models.Order.items))

    if user.get("role") == "ADMIN":
        if user_id is not None:
            query = query.filter(models.Order.user_id == user_id)
    else:
        query = query.filter(models.Order.user_id == int(user["sub"]))

    return query.all()

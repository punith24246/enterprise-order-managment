from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session

from . import models, schemas
from .database import engine, get_db, Base
from .deps import get_current_user, require_admin
from .logging_middleware import CorrelationLoggingMiddleware

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Deliberately not run at import time: importing app.main (e.g. in tests,
    # which override get_db with an in-memory SQLite session) shouldn't force
    # a connection to the real Postgres instance. Uvicorn triggers this
    # lifespan normally in docker-compose / production.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Inventory Service", lifespan=lifespan)
app.add_middleware(CorrelationLoggingMiddleware)


@app.get("/health")
def health():
    return {"status": "ok", "service": "inventory-service"}


@app.get("/products", response_model=list[schemas.ProductResponse])
def list_products(db: Session = Depends(get_db)):
    return db.query(models.Product).all()


@app.get("/products/{product_id}", response_model=schemas.ProductResponse)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@app.post("/products", response_model=schemas.ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: schemas.ProductCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    require_admin(user)
    existing = db.query(models.Product).filter(models.Product.sku == payload.sku).first()
    if existing:
        raise HTTPException(status_code=400, detail="SKU already exists")

    product = models.Product(**payload.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@app.post("/products/{product_id}/adjust-stock", response_model=schemas.ProductResponse)
def adjust_stock(
    product_id: int,
    payload: schemas.StockAdjustRequest,
    db: Session = Depends(get_db),
):
    """Called by order-service to reserve (negative delta) or release (positive
    delta, compensating action on saga failure) stock. Not gated behind admin auth
    because it's an internal service-to-service call in this simplified setup —
    in production this would sit behind network-level trust (VPC/service mesh)
    or a service-to-service auth token, not end-user JWTs.

    Uses SELECT ... FOR UPDATE to take a row-level lock before reading the
    current stock_quantity. Without this, two concurrent requests for the same
    product could both read stock=5, both decide "10 available - 5 = fine",
    and both commit — a classic lost-update race condition that would let you
    oversell. The lock forces the second concurrent request to wait until the
    first transaction commits, so it reads the *post-update* quantity."""
    product = (
        db.query(models.Product)
        .filter(models.Product.id == product_id)
        .with_for_update()
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    new_quantity = product.stock_quantity + payload.delta
    if new_quantity < 0:
        raise HTTPException(status_code=409, detail="Insufficient stock")

    product.stock_quantity = new_quantity
    db.commit()
    db.refresh(product)
    return product

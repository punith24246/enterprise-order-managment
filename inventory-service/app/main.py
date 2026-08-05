from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import models, schemas
from .database import Base, engine, get_db
from .deps import get_current_user, require_admin, require_service_token
from .logging_middleware import CorrelationLoggingMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tests import the app with an overridden database, so real database setup
    # happens when the service starts rather than during import.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Inventory Service", lifespan=lifespan)
app.add_middleware(CorrelationLoggingMiddleware)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ok", "service": "inventory-service", "database": "ok"}


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
    _service: None = Depends(require_service_token),
):
    """Reserve or release stock for the order service.

    The row lock keeps concurrent updates for the same product from overwriting
    each other.
    """
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

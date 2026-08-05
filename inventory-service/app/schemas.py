from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    sku: str = Field(..., min_length=1, max_length=64)
    price: float = Field(..., gt=0, le=1_000_000)
    stock_quantity: int = Field(default=0, ge=0, le=1_000_000)


class ProductResponse(BaseModel):
    id: int
    name: str
    sku: str
    price: float
    stock_quantity: int

    class Config:
        from_attributes = True


class StockAdjustRequest(BaseModel):
    delta: int = Field(..., ge=-100_000, le=100_000)


class StockCheckRequest(BaseModel):
    product_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0, le=10_000)

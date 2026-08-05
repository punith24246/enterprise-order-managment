from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    name: str
    sku: str
    price: float
    stock_quantity: int = 0


class ProductResponse(BaseModel):
    id: int
    name: str
    sku: str
    price: float
    stock_quantity: int

    class Config:
        from_attributes = True


class StockAdjustRequest(BaseModel):
    # positive = restock, negative = deduct
    delta: int = Field(..., description="Amount to add (positive) or remove (negative) from stock")


class StockCheckRequest(BaseModel):
    product_id: int
    quantity: int

from datetime import datetime

from pydantic import BaseModel, Field

from .models import OrderStatus


class OrderItemCreate(BaseModel):
    product_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0, le=10_000)


class OrderCreate(BaseModel):
    items: list[OrderItemCreate] = Field(..., min_length=1, max_length=100)


class OrderItemResponse(BaseModel):
    product_id: int
    quantity: int
    unit_price: float

    class Config:
        from_attributes = True


class OrderResponse(BaseModel):
    id: int
    user_id: int
    status: OrderStatus
    total_amount: float
    created_at: datetime
    failure_reason: str | None
    items: list[OrderItemResponse]

    class Config:
        from_attributes = True

from pydantic import BaseModel
from datetime import datetime
from .models import OrderStatus


class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int


class OrderCreate(BaseModel):
    items: list[OrderItemCreate]


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

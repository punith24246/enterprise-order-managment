import enum
from sqlalchemy import Column, Integer, String, Numeric, ForeignKey, Enum, DateTime, func
from sqlalchemy.orm import relationship
from .database import Base


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


class Order(Base):
    __tablename__ = "order_orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING, nullable=False)
    total_amount = Column(Numeric(10, 2), nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    failure_reason = Column(String, nullable=True)
    # Client-supplied key (e.g. a UUID generated once per user action). If a
    # request with the same key arrives again — network retry, double-click,
    # at-least-once delivery from a queue — we return the original order
    # instead of creating a duplicate. Unique + nullable so old rows / requests
    # without a key aren't affected.
    idempotency_key = Column(String, unique=True, nullable=True, index=True)

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("order_orders.id"), nullable=False)
    product_id = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)

    order = relationship("Order", back_populates="items")

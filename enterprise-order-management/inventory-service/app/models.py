from sqlalchemy import Column, Integer, String, Numeric
from .database import Base


class Product(Base):
    __tablename__ = "inventory_products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    sku = Column(String, unique=True, index=True, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    stock_quantity = Column(Integer, nullable=False, default=0)

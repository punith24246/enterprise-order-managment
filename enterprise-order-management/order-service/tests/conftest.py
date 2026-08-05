import sys
import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import Base
from app import models  # noqa: F401 -- ensures models are registered on Base


@pytest.fixture
def db_session():
    """In-memory SQLite DB per test -- fast, isolated, no real Postgres needed
    to unit test the saga's business logic."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()

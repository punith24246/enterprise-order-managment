import sys
import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import Base, get_db
from app.deps import get_current_user
from app.main import app


@pytest.fixture
def client():
    # StaticPool pins all connections to the same in-memory SQLite DB --
    # without it, each checkout opens a fresh (empty) :memory: database and
    # the tables created below would seem to vanish.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Override auth so tests don't need a real JWT -- FastAPI's
    # dependency_overrides swaps the dependency at the app level, unlike
    # unittest.mock.patch which can't intercept an already-captured Depends().
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: {"role": "ADMIN", "sub": "1"}
    yield TestClient(app)
    app.dependency_overrides.clear()

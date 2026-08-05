from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session

from . import models, schemas, auth_utils
from .database import engine, get_db, Base
from .logging_middleware import CorrelationLoggingMiddleware

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Auth Service", lifespan=lifespan)
app.add_middleware(CorrelationLoggingMiddleware)


@app.get("/health")
def health():
    return {"status": "ok", "service": "auth-service"}


@app.post("/auth/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: schemas.RegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = models.User(
        email=payload.email,
        hashed_password=auth_utils.hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/auth/login", response_model=schemas.TokenResponse)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user or not auth_utils.verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = auth_utils.create_access_token(user.id, user.email, user.role.value)
    return schemas.TokenResponse(access_token=token)


@app.get("/auth/verify")
def verify(token: str):
    """Used internally by the gateway / other services to validate a token."""
    try:
        payload = auth_utils.decode_access_token(token)
        return {"valid": True, "payload": payload}
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

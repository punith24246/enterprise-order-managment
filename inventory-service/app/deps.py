import os

from fastapi import Header, HTTPException
from jose import JWTError, jwt

JWT_SECRET = os.getenv("JWT_SECRET", "supersecretkey_change_me")
JWT_ALGORITHM = "HS256"
INTERNAL_SERVICE_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN", "local-service-token")


def get_current_user(authorization: str = Header(None)):
    """Decode and validate the caller's JWT."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")

    token = authorization.split(" ", 1)[1]
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def require_admin(user: dict) -> None:
    if user.get("role") != "ADMIN":
        raise HTTPException(status_code=403, detail="Admin role required")


def require_service_token(x_service_token: str | None = Header(default=None, alias="X-Service-Token")):
    if not x_service_token or x_service_token != INTERNAL_SERVICE_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid service token")

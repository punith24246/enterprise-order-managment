import os
from fastapi import Header, HTTPException
from jose import jwt, JWTError

JWT_SECRET = os.getenv("JWT_SECRET", "supersecretkey_change_me")
JWT_ALGORITHM = "HS256"


def get_current_user(authorization: str = Header(None)):
    """Services independently verify the JWT signature (shared secret) rather than
    calling back to auth-service on every request — avoids a network hop + single
    point of failure for every downstream call."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


def require_admin(user: dict) -> None:
    if user.get("role") != "ADMIN":
        raise HTTPException(status_code=403, detail="Admin role required")

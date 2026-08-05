from pydantic import BaseModel, EmailStr
from .models import RoleEnum


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    role: RoleEnum = RoleEnum.EMPLOYEE


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    email: EmailStr
    role: RoleEnum

    class Config:
        from_attributes = True

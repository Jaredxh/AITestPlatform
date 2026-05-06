import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserRegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str | None = Field(None, max_length=100)


class UserLoginRequest(BaseModel):
    username: str = Field(..., description="用户名或邮箱")
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class RoleResponse(BaseModel):
    id: uuid.UUID
    name: str
    display_name: str
    description: str | None
    permissions: list[str]
    is_system: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    display_name: str | None
    avatar_url: str | None
    is_active: bool
    is_superuser: bool
    roles: list[RoleResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

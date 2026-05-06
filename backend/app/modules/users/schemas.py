import uuid

from pydantic import BaseModel, EmailStr, Field


class UserCreateRequest(BaseModel):
    """管理员创建用户。"""
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str | None = Field(None, max_length=100)
    is_active: bool = True
    role_ids: list[uuid.UUID] = Field(default_factory=list)


class UserUpdateRequest(BaseModel):
    display_name: str | None = Field(None, max_length=100)
    email: EmailStr | None = None
    is_active: bool | None = None
    password: str | None = Field(None, min_length=6, max_length=128, description="非空则重置密码")


class UserRoleUpdateRequest(BaseModel):
    role_ids: list[uuid.UUID]


class RoleCreateRequest(BaseModel):
    name: str = Field(
        ...,
        min_length=2,
        max_length=50,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="角色标识（仅小写字母/数字/下划线）",
    )
    display_name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdateRequest(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    permissions: list[str] | None = None


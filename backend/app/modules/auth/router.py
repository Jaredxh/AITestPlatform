from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, require_permission
from app.core.response import success_response
from app.modules.auth.models import Role, User
from app.modules.auth.permissions import Permissions
from app.modules.auth.schemas import (
    RefreshTokenRequest,
    RoleResponse,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.modules.auth.service import authenticate_user, create_tokens, register_user

router = APIRouter(prefix="/api/auth", tags=["认证"])


@router.post("/register", response_model=dict)
async def register(data: UserRegisterRequest, db: AsyncSession = Depends(get_db)):
    user = await register_user(db, data)
    return success_response(data=UserResponse.model_validate(user).model_dump(mode="json"), message="注册成功")


@router.post("/login", response_model=dict)
async def login(data: UserLoginRequest, db: AsyncSession = Depends(get_db)):
    user = await authenticate_user(db, data.username, data.password)
    tokens = create_tokens(str(user.id))
    return success_response(
        data={
            "user": UserResponse.model_validate(user).model_dump(mode="json"),
            "tokens": tokens.model_dump(),
        }
    )


@router.post("/refresh", response_model=dict)
async def refresh_token(data: RefreshTokenRequest):
    from app.core.security import decode_token

    payload = decode_token(data.refresh_token)
    if not payload or payload.get("type") != "refresh":
        from app.core.exceptions import UnauthorizedException

        raise UnauthorizedException("无效的刷新令牌")

    tokens = create_tokens(payload["sub"])
    return success_response(data=tokens.model_dump())


@router.get("/me", response_model=dict)
async def get_me(current_user: User = Depends(get_current_user)):
    return success_response(data=UserResponse.model_validate(current_user).model_dump(mode="json"))


@router.get("/roles", response_model=dict)
async def list_roles(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission(Permissions.ROLE_MANAGE)),
):
    result = await db.execute(select(Role).order_by(Role.name))
    roles = result.scalars().all()
    return success_response(
        data=[RoleResponse.model_validate(r).model_dump(mode="json") for r in roles]
    )

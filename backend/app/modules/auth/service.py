from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, UnauthorizedException
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.modules.auth.models import User
from app.modules.auth.schemas import TokenResponse, UserRegisterRequest


async def register_user(db: AsyncSession, data: UserRegisterRequest) -> User:
    stmt = select(User).where(or_(User.username == data.username, User.email == data.email))
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        if existing.username == data.username:
            raise AppException("用户名已存在", code="USERNAME_EXISTS", status_code=409)
        raise AppException("邮箱已被注册", code="EMAIL_EXISTS", status_code=409)

    user = User(
        username=data.username,
        email=data.email,
        hashed_password=hash_password(data.password),
        display_name=data.display_name or data.username,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User:
    """通过用户名或邮箱 + 密码验证用户。"""
    stmt = select(User).where(or_(User.username == username, User.email == username))
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.hashed_password):
        raise UnauthorizedException("用户名或密码错误")

    if not user.is_active:
        raise AppException("账号已被禁用", code="USER_DISABLED", status_code=403)

    return user


def create_tokens(user_id: str) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(subject=user_id),
        refresh_token=create_refresh_token(subject=user_id),
    )


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

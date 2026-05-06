from collections.abc import AsyncGenerator

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedException, UnauthorizedException
from app.core.security import decode_token
from app.database import async_session_factory

security_scheme = HTTPBearer(auto_error=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
):
    if not credentials:
        raise UnauthorizedException("未提供认证令牌")

    payload = decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise UnauthorizedException("无效或过期的令牌")

    from app.modules.auth.service import get_user_by_id

    user = await get_user_by_id(db, payload["sub"])
    if not user:
        raise UnauthorizedException("用户不存在")
    if not user.is_active:
        raise UnauthorizedException("账号已被禁用")
    return user


def require_permission(*permissions: str):
    """依赖注入工厂：检查当前用户是否拥有所有指定权限。

    用法：
        @router.post("/projects")
        async def create_project(user=Depends(require_permission("project:create"))):
            ...
    """

    async def checker(current_user=Depends(get_current_user)):
        if current_user.is_superuser:
            return current_user
        for perm in permissions:
            if not current_user.has_permission(perm):
                raise PermissionDeniedException(f"缺少权限: {perm}")
        return current_user

    return checker

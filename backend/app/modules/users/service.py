import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppException, NotFoundException
from app.core.security import hash_password
from app.modules.auth.models import Role, User
from app.modules.auth.permissions import ALL_PERMISSIONS


async def get_users_paginated(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
) -> tuple[list[User], int]:
    query = select(User).options(selectinload(User.roles))
    count_query = select(func.count()).select_from(User)

    if search:
        pattern = f"%{search}%"
        filter_cond = User.username.ilike(pattern) | User.email.ilike(pattern) | User.display_name.ilike(pattern)
        query = query.where(filter_cond)
        count_query = count_query.where(filter_cond)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    users = list(result.scalars().all())

    return users, total


async def get_user_detail(db: AsyncSession, user_id: uuid.UUID) -> User:
    stmt = select(User).options(selectinload(User.roles)).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundException("用户不存在")
    return user


async def create_user(
    db: AsyncSession,
    *,
    username: str,
    email: str,
    password: str,
    display_name: str | None = None,
    is_active: bool = True,
    role_ids: list[uuid.UUID] | None = None,
) -> User:
    """管理员创建用户。"""
    stmt = select(User).where(or_(User.username == username, User.email == email))
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        if existing.username == username:
            raise AppException("用户名已存在", code="USERNAME_EXISTS", status_code=409)
        raise AppException("邮箱已被使用", code="EMAIL_EXISTS", status_code=409)

    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        display_name=display_name or username,
        is_active=is_active,
    )

    if role_ids:
        roles_result = await db.execute(select(Role).where(Role.id.in_(role_ids)))
        roles = list(roles_result.scalars().all())
        if len(roles) != len(set(role_ids)):
            raise NotFoundException("部分角色不存在")
        user.roles = roles

    db.add(user)
    await db.flush()
    await db.refresh(user, attribute_names=["roles"])
    return user


async def update_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    display_name: str | None = None,
    email: str | None = None,
    is_active: bool | None = None,
    password: str | None = None,
) -> User:
    user = await get_user_detail(db, user_id)

    if email and email != user.email:
        existing = await db.execute(select(User).where(User.email == email, User.id != user_id))
        if existing.scalar_one_or_none():
            raise AppException("邮箱已被使用", code="EMAIL_EXISTS", status_code=409)
        user.email = email

    if display_name is not None:
        user.display_name = display_name
    if is_active is not None:
        user.is_active = is_active
    if password:
        user.hashed_password = hash_password(password)

    await db.flush()
    await db.refresh(user)
    return user


async def update_user_roles(db: AsyncSession, user_id: uuid.UUID, role_ids: list[uuid.UUID]) -> User:
    user = await get_user_detail(db, user_id)

    if role_ids:
        result = await db.execute(select(Role).where(Role.id.in_(role_ids)))
        roles = list(result.scalars().all())
        if len(roles) != len(role_ids):
            raise NotFoundException("部分角色不存在")
        user.roles = roles
    else:
        user.roles = []

    await db.flush()
    await db.refresh(user)
    return user


async def delete_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    user = await get_user_detail(db, user_id)
    if user.is_superuser:
        raise AppException("不能删除超级管理员", code="CANNOT_DELETE_SUPERUSER", status_code=400)
    await db.delete(user)


async def get_all_roles(db: AsyncSession) -> list[Role]:
    result = await db.execute(select(Role).order_by(Role.is_system.desc(), Role.name))
    return list(result.scalars().all())


async def get_role_detail(db: AsyncSession, role_id: uuid.UUID) -> Role:
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if not role:
        raise NotFoundException("角色不存在")
    return role


def _validate_permissions(perms: list[str]) -> list[str]:
    invalid = [p for p in perms if p not in ALL_PERMISSIONS]
    if invalid:
        raise AppException(
            f"未知的权限标识: {', '.join(invalid)}",
            code="INVALID_PERMISSIONS",
            status_code=400,
        )
    return sorted(set(perms))


async def create_role(
    db: AsyncSession,
    *,
    name: str,
    display_name: str,
    description: str | None = None,
    permissions: list[str] | None = None,
) -> Role:
    existing = (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
    if existing:
        raise AppException("角色标识已存在", code="ROLE_NAME_EXISTS", status_code=409)

    role = Role(
        name=name,
        display_name=display_name,
        description=description,
        permissions=_validate_permissions(permissions or []),
        is_system=False,
    )
    db.add(role)
    await db.flush()
    await db.refresh(role)
    return role


async def update_role(
    db: AsyncSession,
    role_id: uuid.UUID,
    *,
    display_name: str | None = None,
    description: str | None = None,
    permissions: list[str] | None = None,
) -> Role:
    role = await get_role_detail(db, role_id)

    if display_name is not None:
        role.display_name = display_name
    if description is not None:
        role.description = description
    if permissions is not None:
        role.permissions = _validate_permissions(permissions)

    await db.flush()
    await db.refresh(role)
    return role


async def delete_role(db: AsyncSession, role_id: uuid.UUID) -> None:
    role = await get_role_detail(db, role_id)
    if role.is_system:
        raise AppException("系统内置角色不可删除", code="CANNOT_DELETE_SYSTEM_ROLE", status_code=400)
    await db.delete(role)

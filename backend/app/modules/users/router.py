import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_permission
from app.core.response import success_response
from app.modules.auth.models import User
from app.modules.auth.permissions import Permissions
from app.modules.auth.schemas import RoleResponse, UserResponse
from app.modules.users.schemas import (
    RoleCreateRequest,
    RoleUpdateRequest,
    UserCreateRequest,
    UserRoleUpdateRequest,
    UserUpdateRequest,
)
from app.modules.users.service import (
    create_role,
    create_user,
    delete_role,
    delete_user,
    get_all_roles,
    get_role_detail,
    get_user_detail,
    get_users_paginated,
    update_role,
    update_user,
    update_user_roles,
)

router = APIRouter(prefix="/api/users", tags=["用户管理"])


@router.get("", response_model=dict)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission(Permissions.USER_MANAGE)),
):
    users, total = await get_users_paginated(db, page, page_size, search)
    return success_response(
        data={
            "items": [UserResponse.model_validate(u).model_dump(mode="json") for u in users],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.post("", response_model=dict)
async def create_new_user(
    data: UserCreateRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission(Permissions.USER_MANAGE)),
):
    user = await create_user(
        db,
        username=data.username,
        email=str(data.email),
        password=data.password,
        display_name=data.display_name,
        is_active=data.is_active,
        role_ids=data.role_ids,
    )
    return success_response(
        data=UserResponse.model_validate(user).model_dump(mode="json"),
        message="用户已创建",
    )


@router.get("/roles", response_model=dict)
async def list_all_roles(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission(Permissions.USER_MANAGE)),
):
    roles = await get_all_roles(db)
    return success_response(
        data=[RoleResponse.model_validate(r).model_dump(mode="json") for r in roles]
    )


@router.post("/roles", response_model=dict)
async def create_new_role(
    data: RoleCreateRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission(Permissions.ROLE_MANAGE)),
):
    role = await create_role(
        db,
        name=data.name,
        display_name=data.display_name,
        description=data.description,
        permissions=data.permissions,
    )
    return success_response(
        data=RoleResponse.model_validate(role).model_dump(mode="json"),
        message="角色已创建",
    )


@router.get("/roles/{role_id}", response_model=dict)
async def get_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission(Permissions.ROLE_MANAGE)),
):
    role = await get_role_detail(db, role_id)
    return success_response(data=RoleResponse.model_validate(role).model_dump(mode="json"))


@router.patch("/roles/{role_id}", response_model=dict)
async def patch_role(
    role_id: uuid.UUID,
    data: RoleUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission(Permissions.ROLE_MANAGE)),
):
    role = await update_role(
        db,
        role_id,
        display_name=data.display_name,
        description=data.description,
        permissions=data.permissions,
    )
    return success_response(
        data=RoleResponse.model_validate(role).model_dump(mode="json"),
        message="角色已更新",
    )


@router.delete("/roles/{role_id}", response_model=dict)
async def remove_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission(Permissions.ROLE_MANAGE)),
):
    await delete_role(db, role_id)
    return success_response(message="角色已删除")


@router.get("/{user_id}", response_model=dict)
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission(Permissions.USER_MANAGE)),
):
    user = await get_user_detail(db, user_id)
    return success_response(data=UserResponse.model_validate(user).model_dump(mode="json"))


@router.patch("/{user_id}", response_model=dict)
async def patch_user(
    user_id: uuid.UUID,
    data: UserUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission(Permissions.USER_MANAGE)),
):
    user = await update_user(
        db, user_id,
        display_name=data.display_name,
        email=str(data.email) if data.email else None,
        is_active=data.is_active,
        password=data.password,
    )
    return success_response(data=UserResponse.model_validate(user).model_dump(mode="json"))


@router.put("/{user_id}/roles", response_model=dict)
async def set_user_roles(
    user_id: uuid.UUID,
    data: UserRoleUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission(Permissions.ROLE_MANAGE)),
):
    user = await update_user_roles(db, user_id, data.role_ids)
    return success_response(data=UserResponse.model_validate(user).model_dump(mode="json"))


@router.delete("/{user_id}", response_model=dict)
async def remove_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission(Permissions.USER_MANAGE)),
):
    await delete_user(db, user_id)
    return success_response(message="用户已删除")

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, require_permission
from app.core.response import success_response
from app.modules.auth.models import User
from app.modules.auth.permissions import Permissions
from app.modules.projects.schemas import (
    MemberAddRequest,
    ProjectCreateRequest,
    ProjectUpdateRequest,
)
from app.modules.projects.service import (
    add_member,
    create_project,
    delete_project,
    get_project_detail,
    get_projects_for_user,
    remove_member,
    update_project,
)

router = APIRouter(prefix="/api/projects", tags=["项目管理"])


@router.get("", response_model=dict)
async def list_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.PROJECT_VIEW)),
):
    projects, total = await get_projects_for_user(db, current_user, page, page_size, search)
    return success_response(
        data={
            "items": [p.model_dump(mode="json") for p in projects],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.post("", response_model=dict)
async def create(
    data: ProjectCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.PROJECT_CREATE)),
):
    project = await create_project(db, data, current_user)
    detail = await get_project_detail(db, project.id, current_user)
    return success_response(data=detail.model_dump(mode="json"), message="项目创建成功")


@router.get("/{project_id}", response_model=dict)
async def get_detail(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    detail = await get_project_detail(db, project_id, current_user)
    return success_response(data=detail.model_dump(mode="json"))


@router.patch("/{project_id}", response_model=dict)
async def patch_project(
    project_id: uuid.UUID,
    data: ProjectUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.PROJECT_EDIT)),
):
    project = await update_project(db, project_id, data, current_user)
    return success_response(data=project.model_dump(mode="json"))


@router.delete("/{project_id}", response_model=dict)
async def remove_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.PROJECT_DELETE)),
):
    await delete_project(db, project_id, current_user)
    return success_response(message="项目已删除")


@router.post("/{project_id}/members", response_model=dict)
async def add_project_member(
    project_id: uuid.UUID,
    data: MemberAddRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    member = await add_member(db, project_id, data.user_id, data.role, current_user)
    return success_response(data=member.model_dump(mode="json"), message="成员添加成功")


@router.delete("/{project_id}/members/{user_id}", response_model=dict)
async def remove_project_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await remove_member(db, project_id, user_id, current_user)
    return success_response(message="成员已移除")

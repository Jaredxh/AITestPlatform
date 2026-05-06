import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppException, NotFoundException, PermissionDeniedException
from app.modules.auth.models import User
from app.modules.projects.models import Project, ProjectMember
from app.modules.projects.schemas import (
    MemberResponse,
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)


def _to_project_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        owner_id=project.owner_id,
        owner_name=project.owner.display_name if project.owner else None,
        member_count=len(project.members),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _to_member_response(m: ProjectMember) -> MemberResponse:
    return MemberResponse(
        user_id=m.user_id,
        username=m.user.username if m.user else "",
        display_name=m.user.display_name if m.user else None,
        role=m.role,
        joined_at=m.joined_at,
    )


def _to_detail_response(project: Project) -> ProjectDetailResponse:
    resp = _to_project_response(project)
    return ProjectDetailResponse(
        **resp.model_dump(),
        members=[_to_member_response(m) for m in project.members],
    )


async def create_project(db: AsyncSession, data: ProjectCreateRequest, owner: User) -> Project:
    project = Project(
        name=data.name,
        description=data.description,
        owner_id=owner.id,
    )
    db.add(project)
    await db.flush()

    member = ProjectMember(project_id=project.id, user_id=owner.id, role="owner")
    db.add(member)
    await db.flush()

    from app.modules.prompts.service import init_project_prompts
    await init_project_prompts(db, project.id, owner.id)

    await db.refresh(project)
    return project


async def get_projects_for_user(
    db: AsyncSession,
    user: User,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
) -> tuple[list[ProjectResponse], int]:
    """返回用户有权访问的项目列表。superuser 看所有项目，普通用户只看自己参与的。"""
    query = select(Project).options(selectinload(Project.members), selectinload(Project.owner))
    count_query = select(func.count()).select_from(Project)

    if not user.is_superuser:
        member_subq = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
        query = query.where(Project.id.in_(member_subq))
        count_query = count_query.where(Project.id.in_(member_subq))

    if search:
        pattern = f"%{search}%"
        query = query.where(Project.name.ilike(pattern))
        count_query = count_query.where(Project.name.ilike(pattern))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(Project.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    projects = list(result.scalars().unique().all())

    return [_to_project_response(p) for p in projects], total


async def get_project_detail(db: AsyncSession, project_id: uuid.UUID, user: User) -> ProjectDetailResponse:
    project = await _get_project_or_404(db, project_id)
    _check_member_access(project, user)
    return _to_detail_response(project)


async def update_project(
    db: AsyncSession, project_id: uuid.UUID, data: ProjectUpdateRequest, user: User
) -> ProjectResponse:
    project = await _get_project_or_404(db, project_id)
    _check_project_admin(project, user)

    if data.name is not None:
        project.name = data.name
    if data.description is not None:
        project.description = data.description
    if data.status is not None:
        project.status = data.status

    await db.flush()
    await db.refresh(project)
    return _to_project_response(project)


async def delete_project(db: AsyncSession, project_id: uuid.UUID, user: User) -> None:
    project = await _get_project_or_404(db, project_id)
    if project.owner_id != user.id and not user.is_superuser:
        raise PermissionDeniedException("只有项目所有者或超管可以删除项目")
    await db.delete(project)


async def add_member(
    db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID, role: str, current_user: User
) -> MemberResponse:
    project = await _get_project_or_404(db, project_id)
    _check_project_admin(project, current_user)

    existing = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        )
    )
    if existing.scalar_one_or_none():
        raise AppException("该用户已是项目成员", code="ALREADY_MEMBER", status_code=409)

    target_user = await db.execute(select(User).where(User.id == user_id))
    if not target_user.scalar_one_or_none():
        raise NotFoundException("用户不存在")

    member = ProjectMember(project_id=project_id, user_id=user_id, role=role)
    db.add(member)
    await db.flush()
    await db.refresh(member)
    return _to_member_response(member)


async def remove_member(
    db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID, current_user: User
) -> None:
    project = await _get_project_or_404(db, project_id)
    _check_project_admin(project, current_user)

    if project.owner_id == user_id:
        raise AppException("不能移除项目所有者", code="CANNOT_REMOVE_OWNER", status_code=400)

    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise NotFoundException("该用户不是项目成员")
    await db.delete(member)


async def _get_project_or_404(db: AsyncSession, project_id: uuid.UUID) -> Project:
    stmt = (
        select(Project)
        .options(selectinload(Project.members).selectinload(ProjectMember.user), selectinload(Project.owner))
        .where(Project.id == project_id)
    )
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()
    if not project:
        raise NotFoundException("项目不存在")
    return project


def _check_member_access(project: Project, user: User) -> None:
    if user.is_superuser:
        return
    member_ids = {m.user_id for m in project.members}
    if user.id not in member_ids:
        raise PermissionDeniedException("你不是该项目的成员")


def _check_project_admin(project: Project, user: User) -> None:
    """检查用户是否是项目 owner/admin 或系统超管。"""
    if user.is_superuser:
        return
    for m in project.members:
        if m.user_id == user.id and m.role in ("owner", "admin"):
            return
    raise PermissionDeniedException("需要项目管理员权限")

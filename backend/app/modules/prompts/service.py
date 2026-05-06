"""提示词管理服务：CRUD、版本管理、模板渲染、内置模板初始化。"""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppException, NotFoundException, PermissionDeniedException
from app.modules.auth.models import User
from app.modules.prompts.built_in import BUILT_IN_PROMPTS
from app.modules.prompts.models import PromptTemplate, PromptVersion
from app.modules.prompts.schemas import (
    PromptCreateRequest,
    PromptListResponse,
    PromptResponse,
    PromptUpdateRequest,
    PromptVersionResponse,
)


def _to_response(t: PromptTemplate) -> PromptResponse:
    return PromptResponse(
        id=t.id,
        project_id=t.project_id,
        name=t.name,
        description=t.description,
        content=t.content,
        category=t.category,
        sub_category=t.sub_category,
        is_system=t.is_system,
        is_default=t.is_default,
        auto_apply=t.auto_apply,
        variables=t.variables,
        version=t.version,
        created_by=t.created_by,
        creator_name=t.creator.display_name or t.creator.username if t.creator else None,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _to_list_response(t: PromptTemplate) -> PromptListResponse:
    return PromptListResponse(
        id=t.id,
        name=t.name,
        description=t.description,
        category=t.category,
        sub_category=t.sub_category,
        is_system=t.is_system,
        is_default=t.is_default,
        auto_apply=t.auto_apply,
        version=t.version,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _to_version_response(v: PromptVersion) -> PromptVersionResponse:
    return PromptVersionResponse(
        id=v.id,
        template_id=v.template_id,
        version=v.version,
        content=v.content,
        change_note=v.change_note,
        created_by=v.created_by,
        creator_name=v.creator.display_name or v.creator.username if v.creator else None,
        created_at=v.created_at,
    )


# ── CRUD ──

async def list_prompts(
    db: AsyncSession,
    project_id: uuid.UUID,
    category: str | None = None,
) -> list[PromptListResponse]:
    query = select(PromptTemplate).where(PromptTemplate.project_id == project_id)
    if category:
        query = query.where(PromptTemplate.category == category)
    query = query.order_by(PromptTemplate.category, PromptTemplate.is_default.desc(), PromptTemplate.name)
    result = await db.execute(query)
    templates = list(result.scalars().unique().all())
    return [_to_list_response(t) for t in templates]


async def create_prompt(
    db: AsyncSession,
    project_id: uuid.UUID,
    data: PromptCreateRequest,
    user: User,
) -> PromptResponse:
    if data.is_default:
        await _clear_category_default(db, project_id, data.category)

    template = PromptTemplate(
        project_id=project_id,
        name=data.name,
        description=data.description,
        content=data.content,
        category=data.category,
        sub_category=data.sub_category,
        is_system=False,
        is_default=data.is_default,
        auto_apply=data.auto_apply,
        variables=[v.model_dump() for v in data.variables],
        version=1,
        created_by=user.id,
    )
    db.add(template)
    await db.flush()

    version = PromptVersion(
        template_id=template.id,
        version=1,
        content=data.content,
        change_note="初始创建",
        created_by=user.id,
    )
    db.add(version)
    await db.flush()
    await db.refresh(template)
    return _to_response(template)


async def get_prompt(db: AsyncSession, prompt_id: uuid.UUID) -> PromptResponse:
    template = await _get_or_404(db, prompt_id)
    return _to_response(template)


async def update_prompt(
    db: AsyncSession,
    prompt_id: uuid.UUID,
    data: PromptUpdateRequest,
    user: User,
) -> PromptResponse:
    template = await _get_or_404(db, prompt_id)

    content_changed = data.content is not None and data.content != template.content

    if data.name is not None:
        template.name = data.name
    if data.description is not None:
        template.description = data.description
    if data.category is not None:
        template.category = data.category
    if data.sub_category is not None:
        template.sub_category = data.sub_category
    if data.auto_apply is not None:
        template.auto_apply = data.auto_apply
    if data.variables is not None:
        template.variables = [v.model_dump() for v in data.variables]
    if data.is_default is not None:
        if data.is_default:
            await _clear_category_default(db, template.project_id, template.category)
        template.is_default = data.is_default

    if content_changed:
        template.content = data.content
        template.version += 1
        version = PromptVersion(
            template_id=template.id,
            version=template.version,
            content=data.content,
            change_note=data.change_note or f"更新至 v{template.version}",
            created_by=user.id,
        )
        db.add(version)

    await db.flush()
    await db.refresh(template)
    return _to_response(template)


async def delete_prompt(db: AsyncSession, prompt_id: uuid.UUID, user: User) -> None:
    template = await _get_or_404(db, prompt_id)
    if template.is_system:
        raise AppException("系统内置提示词不可删除，只能编辑", code="SYSTEM_PROMPT", status_code=403)
    await db.delete(template)


async def set_default(db: AsyncSession, prompt_id: uuid.UUID) -> PromptResponse:
    template = await _get_or_404(db, prompt_id)
    await _clear_category_default(db, template.project_id, template.category)
    template.is_default = True
    await db.flush()
    await db.refresh(template)
    return _to_response(template)


# ── 版本管理 ──

async def list_versions(
    db: AsyncSession,
    prompt_id: uuid.UUID,
) -> list[PromptVersionResponse]:
    await _get_or_404(db, prompt_id)
    result = await db.execute(
        select(PromptVersion)
        .where(PromptVersion.template_id == prompt_id)
        .order_by(PromptVersion.version.desc())
    )
    versions = list(result.scalars().all())
    return [_to_version_response(v) for v in versions]


# ── 模板渲染 ──

def render_template(content: str, variables: dict[str, str]) -> str:
    """将 {{变量名}} 替换为实际值。未提供的变量保留原占位符。"""
    def replacer(match: re.Match) -> str:
        key = match.group(1).strip()
        return variables.get(key, match.group(0))

    return re.sub(r"\{\{(.+?)\}\}", replacer, content)


# ── 项目初始化 ──

async def init_project_prompts(
    db: AsyncSession,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
) -> int:
    """为项目同步内置提示词模板：以 (category, sub_category) 为标识做 upsert，
    保证更新 ``built_in.py`` 后已有项目的内置模板也会被覆盖刷新。
    返回新建 + 更新的总条数。
    """
    existing_stmt = select(PromptTemplate).where(
        PromptTemplate.project_id == project_id,
        PromptTemplate.is_system.is_(True),
    )
    existing_rows = list((await db.execute(existing_stmt)).scalars().all())
    by_key: dict[tuple[str, str | None], PromptTemplate] = {
        (r.category, r.sub_category): r for r in existing_rows
    }

    touched = 0
    for tmpl_data in BUILT_IN_PROMPTS:
        key = (tmpl_data["category"], tmpl_data.get("sub_category"))
        existing_row = by_key.get(key)
        if existing_row:
            if (
                existing_row.name != tmpl_data["name"]
                or existing_row.description != tmpl_data.get("description")
                or existing_row.content != tmpl_data["content"]
                or existing_row.variables != tmpl_data.get("variables", [])
            ):
                existing_row.name = tmpl_data["name"]
                existing_row.description = tmpl_data.get("description")
                existing_row.content = tmpl_data["content"]
                existing_row.variables = tmpl_data.get("variables", [])
                existing_row.version = (existing_row.version or 1) + 1
                touched += 1
        else:
            template = PromptTemplate(
                project_id=project_id,
                name=tmpl_data["name"],
                description=tmpl_data.get("description"),
                content=tmpl_data["content"],
                category=tmpl_data["category"],
                sub_category=tmpl_data.get("sub_category"),
                is_system=True,
                is_default=tmpl_data.get("is_default", False),
                auto_apply=tmpl_data.get("auto_apply", False),
                variables=tmpl_data.get("variables", []),
                version=1,
                created_by=user_id,
            )
            db.add(template)
            touched += 1

    await db.flush()
    return touched


# ── Internal ──

async def _get_or_404(db: AsyncSession, prompt_id: uuid.UUID) -> PromptTemplate:
    result = await db.execute(
        select(PromptTemplate).where(PromptTemplate.id == prompt_id)
    )
    template = result.scalar_one_or_none()
    if not template:
        raise NotFoundException("提示词模板不存在")
    return template


async def _clear_category_default(
    db: AsyncSession, project_id: uuid.UUID, category: str
) -> None:
    """确保同一项目同一分类下只有一个默认。"""
    result = await db.execute(
        select(PromptTemplate).where(
            PromptTemplate.project_id == project_id,
            PromptTemplate.category == category,
            PromptTemplate.is_default.is_(True),
        )
    )
    for t in result.scalars().all():
        t.is_default = False

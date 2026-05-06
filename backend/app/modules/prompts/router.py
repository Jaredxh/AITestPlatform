import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_permission
from app.core.response import success_response
from app.modules.auth.models import User
from app.modules.auth.permissions import Permissions
from app.modules.prompts.schemas import PromptCreateRequest, PromptUpdateRequest
from app.modules.prompts.service import (
    create_prompt,
    delete_prompt,
    get_prompt,
    init_project_prompts,
    list_prompts,
    list_versions,
    set_default,
    update_prompt,
)

router = APIRouter(prefix="/api", tags=["提示词管理"])


# 2026-05：把 ``REQUIREMENT_*`` 一刀切替换成专属 ``PROMPT_*`` 权限。
# 业务原因：提示词模板和需求文档完全是两个领域，复用 REQUIREMENT_* 导致
# 角色配置时找不到"提示词管理"开关（用户验收反馈）；同时 admin 把 prompt
# 编辑权和需求上传权绑死也不合理（PM 要写 prompt 不一定要碰需求）。
# 兼容性：``init_data._seed_roles`` 启动自动同步系统角色，admin /
# project_manager 已自动获得新权限，无需手写迁移。


@router.get("/projects/{project_id}/prompts", response_model=dict)
async def list_project_prompts(
    project_id: uuid.UUID,
    category: str | None = Query(None, description="按分类筛选: chat, review, generation, ui_test, custom"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.PROMPT_VIEW)),
):
    """获取项目下的提示词列表，支持按 category 筛选。"""
    prompts = await list_prompts(db, project_id, category)
    return success_response(data=[p.model_dump(mode="json") for p in prompts])


@router.post("/projects/{project_id}/prompts", response_model=dict)
async def create_project_prompt(
    project_id: uuid.UUID,
    data: PromptCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.PROMPT_EDIT)),
):
    """创建提示词模板。"""
    prompt = await create_prompt(db, project_id, data, current_user)
    return success_response(data=prompt.model_dump(mode="json"), message="提示词创建成功")


@router.post("/projects/{project_id}/prompts/init", response_model=dict)
async def init_prompts(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.PROMPT_EDIT)),
):
    """手动为项目初始化内置提示词（通常在项目创建时自动执行）。"""
    count = await init_project_prompts(db, project_id, current_user.id)
    if count == 0:
        return success_response(message="内置提示词已存在，无需重复初始化")
    return success_response(data={"created_count": count}, message=f"已初始化 {count} 个内置提示词")


@router.get("/prompts/{prompt_id}", response_model=dict)
async def get_prompt_detail(
    prompt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.PROMPT_VIEW)),
):
    """获取提示词详情。"""
    prompt = await get_prompt(db, prompt_id)
    return success_response(data=prompt.model_dump(mode="json"))


@router.patch("/prompts/{prompt_id}", response_model=dict)
async def update_prompt_detail(
    prompt_id: uuid.UUID,
    data: PromptUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.PROMPT_EDIT)),
):
    """编辑提示词（修改 content 时自动保存版本）。"""
    prompt = await update_prompt(db, prompt_id, data, current_user)
    return success_response(data=prompt.model_dump(mode="json"), message="提示词更新成功")


@router.delete("/prompts/{prompt_id}", response_model=dict)
async def remove_prompt(
    prompt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.PROMPT_DELETE)),
):
    """删除提示词（系统内置不可删除）。"""
    await delete_prompt(db, prompt_id, current_user)
    return success_response(message="提示词已删除")


@router.get("/prompts/{prompt_id}/versions", response_model=dict)
async def get_prompt_versions(
    prompt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.PROMPT_VIEW)),
):
    """获取提示词的版本历史。"""
    versions = await list_versions(db, prompt_id)
    return success_response(data=[v.model_dump(mode="json") for v in versions])


@router.post("/prompts/{prompt_id}/set-default", response_model=dict)
async def set_prompt_default(
    prompt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.PROMPT_EDIT)),
):
    """设为该分类下的默认提示词。"""
    prompt = await set_default(db, prompt_id)
    return success_response(data=prompt.model_dump(mode="json"), message="已设为默认")

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, require_permission
from app.core.response import success_response
from app.modules.auth.models import User
from app.modules.auth.permissions import Permissions
from app.modules.llm.providers import test_connection
from app.modules.llm.schemas import (
    LLMConfigCreateRequest,
    LLMConfigTestRequest,
    LLMConfigUpdateRequest,
)
from app.modules.llm.service import (
    create_llm_config,
    delete_llm_config,
    get_llm_config,
    get_llm_config_with_key,
    list_llm_configs,
    update_llm_config,
)

router = APIRouter(prefix="/api/llm-configs", tags=["LLM 配置"])
legacy_router = APIRouter(prefix="/api/llm", tags=["LLM 配置兼容"])


@router.get("", response_model=dict)
async def list_configs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    configs = await list_llm_configs(db)
    return success_response(data=[c.model_dump(mode="json") for c in configs])


@legacy_router.get("/configs", response_model=dict)
async def list_configs_legacy(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Compatibility alias for older frontend code: /api/llm/configs."""
    return await list_configs(db, current_user)


@router.post("", response_model=dict)
async def create_config(
    data: LLMConfigCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.LLM_CONFIG)),
):
    config = await create_llm_config(db, data, current_user)
    return success_response(data=config.model_dump(mode="json"), message="LLM 配置创建成功")


@router.get("/{config_id}", response_model=dict)
async def get_config(
    config_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    config = await get_llm_config(db, config_id)
    return success_response(data=config.model_dump(mode="json"))


@router.patch("/{config_id}", response_model=dict)
async def patch_config(
    config_id: uuid.UUID,
    data: LLMConfigUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.LLM_CONFIG)),
):
    config = await update_llm_config(db, config_id, data)
    return success_response(data=config.model_dump(mode="json"), message="LLM 配置已更新")


@router.delete("/{config_id}", response_model=dict)
async def remove_config(
    config_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.LLM_CONFIG)),
):
    await delete_llm_config(db, config_id)
    return success_response(message="LLM 配置已删除")


@router.post("/{config_id}/test", response_model=dict)
async def test_saved_config(
    config_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """测试已保存配置的连通性。"""
    config, api_key = await get_llm_config_with_key(db, config_id)
    result = await test_connection(
        provider=config.provider,
        model=config.model,
        api_key=api_key,
        base_url=config.base_url,
    )
    return success_response(data=result)


@router.post("/test", response_model=dict)
async def test_new_config(
    data: LLMConfigTestRequest,
    current_user: User = Depends(require_permission(Permissions.LLM_CONFIG)),
):
    """直接测试一组配置参数的连通性（无需先保存）。"""
    result = await test_connection(
        provider=data.provider,
        model=data.model,
        api_key=data.api_key,
        base_url=data.base_url,
    )
    return success_response(data=result)

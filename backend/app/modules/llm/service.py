import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
from app.core.exceptions import NotFoundException
from app.modules.auth.models import User
from app.modules.llm.models import LLMConfig
from app.modules.llm.schemas import (
    LLMConfigCreateRequest,
    LLMConfigResponse,
    LLMConfigUpdateRequest,
)


def _to_response(config: LLMConfig) -> LLMConfigResponse:
    return LLMConfigResponse(
        id=config.id,
        name=config.name,
        provider=config.provider,
        model=config.model,
        base_url=config.base_url,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        is_default=config.is_default,
        has_api_key=bool(config.api_key_encrypted),
        created_by=config.created_by,
        creator_name=config.creator.display_name if config.creator else None,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


async def list_llm_configs(db: AsyncSession) -> list[LLMConfigResponse]:
    result = await db.execute(
        select(LLMConfig).order_by(LLMConfig.is_default.desc(), LLMConfig.created_at.desc())
    )
    configs = list(result.scalars().all())
    return [_to_response(c) for c in configs]


async def get_llm_config(db: AsyncSession, config_id: uuid.UUID) -> LLMConfigResponse:
    config = await _get_or_404(db, config_id)
    return _to_response(config)


async def get_llm_config_with_key(db: AsyncSession, config_id: uuid.UUID) -> tuple[LLMConfig, str | None]:
    """返回原始模型与解密后的 API Key（供内部使用，不暴露给 API）。"""
    config = await _get_or_404(db, config_id)
    api_key = decrypt(config.api_key_encrypted) if config.api_key_encrypted else None
    return config, api_key


async def create_llm_config(
    db: AsyncSession, data: LLMConfigCreateRequest, user: User
) -> LLMConfigResponse:
    if data.is_default:
        await _clear_default(db)

    config = LLMConfig(
        name=data.name,
        provider=data.provider,
        model=data.model,
        api_key_encrypted=encrypt(data.api_key) if data.api_key else None,
        base_url=data.base_url,
        temperature=data.temperature,
        max_tokens=data.max_tokens,
        is_default=data.is_default,
        created_by=user.id,
    )
    db.add(config)
    await db.flush()
    await db.refresh(config)
    return _to_response(config)


async def update_llm_config(
    db: AsyncSession, config_id: uuid.UUID, data: LLMConfigUpdateRequest
) -> LLMConfigResponse:
    config = await _get_or_404(db, config_id)

    if data.name is not None:
        config.name = data.name
    if data.provider is not None:
        config.provider = data.provider
    if data.model is not None:
        config.model = data.model
    if data.api_key is not None:
        config.api_key_encrypted = encrypt(data.api_key) if data.api_key else None
    if data.base_url is not None:
        config.base_url = data.base_url
    if data.temperature is not None:
        config.temperature = data.temperature
    if data.max_tokens is not None:
        config.max_tokens = data.max_tokens
    if data.is_default is not None:
        if data.is_default:
            await _clear_default(db)
        config.is_default = data.is_default

    await db.flush()
    await db.refresh(config)
    return _to_response(config)


async def delete_llm_config(db: AsyncSession, config_id: uuid.UUID) -> None:
    config = await _get_or_404(db, config_id)
    await db.delete(config)


async def _get_or_404(db: AsyncSession, config_id: uuid.UUID) -> LLMConfig:
    result = await db.execute(select(LLMConfig).where(LLMConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise NotFoundException("LLM 配置不存在")
    return config


async def _clear_default(db: AsyncSession) -> None:
    """将已有的 is_default=True 的配置设为 False，保证唯一默认配置。"""
    result = await db.execute(select(LLMConfig).where(LLMConfig.is_default.is_(True)))
    for c in result.scalars().all():
        c.is_default = False

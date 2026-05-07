import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class LLMConfigCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    provider: str = Field(..., pattern=r"^(openai|deepseek|qwen|ollama|custom)$")
    model: str = Field(..., min_length=1, max_length=100)
    api_key: str | None = Field(None, min_length=1)
    base_url: str | None = None
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    # 仅做基础合理性校验，不再限定上限（不同模型可达 200K / 1M / 2M tokens）
    max_tokens: int = Field(4096, ge=1, le=10_000_000)
    is_default: bool = False


class LLMConfigUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    provider: str | None = Field(None, pattern=r"^(openai|deepseek|qwen|ollama|custom)$")
    model: str | None = Field(None, min_length=1, max_length=100)
    api_key: str | None = None
    base_url: str | None = None
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(None, ge=1, le=10_000_000)
    is_default: bool | None = None


class LLMConfigResponse(BaseModel):
    id: uuid.UUID
    name: str
    provider: str
    model: str
    base_url: str | None
    temperature: float
    max_tokens: int
    is_default: bool
    has_api_key: bool
    created_by: uuid.UUID
    creator_name: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LLMConfigTestRequest(BaseModel):
    """直接测试（不需要已保存配置）。"""
    provider: str = Field(..., pattern=r"^(openai|deepseek|qwen|ollama|custom)$")
    model: str = Field(..., min_length=1, max_length=100)
    api_key: str | None = None
    base_url: str | None = None


class LLMConfigTestResponse(BaseModel):
    success: bool
    message: str
    model: str | None = None
    response_time_ms: int | None = None


# ===== Chat Schemas =====


class ChatSessionCreateRequest(BaseModel):
    title: str | None = Field(None, max_length=200)
    llm_config_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    system_prompt: str | None = None


class ChatSessionUpdateRequest(BaseModel):
    title: str | None = Field(None, max_length=200)
    llm_config_id: uuid.UUID | None = None
    system_prompt: str | None = None


class ChatMessageResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: str
    tokens_used: int | None
    model_used: str | None
    meta_data: dict | None = None
    #: Phase 12 / Task 12.6 — 该消息触发的 skill 调用日志 id（前端 SkillUsageBadge 用）
    skill_invocation_id: uuid.UUID | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatSessionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID | None
    title: str | None
    llm_config_id: uuid.UUID | None
    llm_config_name: str | None = None
    system_prompt: str | None
    message_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChatSessionDetailResponse(ChatSessionResponse):
    messages: list[ChatMessageResponse] = []


class ChatSendRequest(BaseModel):
    content: str = Field(..., min_length=1)
    llm_config_id: uuid.UUID | None = None
    # Deprecated: kept for backward compatibility. The agent now always has
    # tool access and autonomously decides when to search.
    web_search: bool = True

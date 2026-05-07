"""Skill 模块 Pydantic schemas（Phase 12 / Task 12.1）。

含三类对象：

1. HTTP 请求 / 响应：``SkillCreateRequest`` / ``SkillUpdateRequest`` /
   ``SkillResponse`` / ``SkillListResponse`` / ``SkillVersionResponse`` /
   ``SkillUsageLogResponse`` / ``SkillSafetyScanResponse``
2. 内部传递的 dataclass：``SkillImportPreview``（解析 ZIP 后给前端预览）
3. Debug / 工具用：``MatchTriggerRequest`` / ``MatchTriggerResponse``

OpenClaw 字段映射（来自 SKILL.md YAML 前言）：

- ``name`` / ``description`` / ``triggers`` / ``tools_required`` 都是 OpenClaw
  原生字段，本模块直接透传；
- ``activation_mode`` / ``slug`` / ``category`` / ``tags`` 是平台扩展，
  导出时同时写入 SKILL.md YAML 前言（OpenClaw 不识别的字段不影响其加载）。

ORM 属性 ``Skill.extra_metadata`` ↔ DB 列 ``metadata`` ↔ Pydantic 字段
``metadata``：通过 ``Field(validation_alias="extra_metadata")`` 让 Pydantic
能从 ORM 实例反读，对外 dump 仍输出 ``metadata``。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

# ──────────────────────────────────────────────────────────────────────────────
# 公共字段约束
# ──────────────────────────────────────────────────────────────────────────────

ACTIVATION_MODES = ("manual", "trigger", "agent_callable", "always", "auto_apply")
SOURCES = ("built_in", "imported", "custom")
SAFETY_STATUSES = ("unscanned", "clean", "warning", "blocked")
USAGE_REASONS = ("manual", "trigger_match", "agent_callable", "always", "auto_apply")
USAGE_OUTCOMES = ("success", "failed", "no_output", "user_cancelled")

_SLUG_PATTERN = r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$"

# ``metadata`` 字段在 ORM 上叫 ``extra_metadata``，本别名让 Pydantic
# 既能从 ORM 实例（``extra_metadata``）也能从外部输入（``metadata``）读到。
#
# 顺序很重要：``extra_metadata`` 必须在前 ——
# 因为 SQLAlchemy ``DeclarativeBase`` 子类有 class-level ``metadata``
# 属性指向 ``Base.metadata``，from_attributes 模式下若优先匹配到它，
# 会拿到一个 ``MetaData`` 对象而非 dict，触发 list_type 校验失败。
_METADATA_ALIASES = AliasChoices("extra_metadata", "metadata")


# ──────────────────────────────────────────────────────────────────────────────
# Skill 主体
# ──────────────────────────────────────────────────────────────────────────────


class SkillCreateRequest(BaseModel):
    """创建 skill（手动新建路径，导入 ZIP 走另外一条 importer 路径）。"""

    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=100, pattern=_SLUG_PATTERN)
    description: str = Field(..., min_length=1)
    semantic_version: str = Field("1.0.0", max_length=20)
    category: str = Field("custom", max_length=50)
    tags: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    tools_required: list[str] = Field(default_factory=list)
    activation_mode: str = Field("agent_callable")
    body: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    attachments: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("activation_mode")
    @classmethod
    def _check_activation_mode(cls, v: str) -> str:
        if v not in ACTIVATION_MODES:
            raise ValueError(f"activation_mode must be one of {ACTIVATION_MODES}")
        return v

    @field_validator("slug")
    @classmethod
    def _slug_not_system(cls, v: str) -> str:
        # ``system_*`` 命名空间锁定在 service 层做更彻底的校验（含调用方判断）；
        # schema 层只做最浅的快速失败提示，避免 service 层兜底之前用户拿到 500。
        if v.startswith("system_"):
            raise ValueError("slug starting with 'system_' is reserved for built-in skills")
        return v


class SkillUpdateRequest(BaseModel):
    """部分更新；任一字段缺省则不变。``slug`` 不允许更新（避免外部引用错位）。"""

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, min_length=1)
    semantic_version: str | None = Field(None, max_length=20)
    category: str | None = Field(None, max_length=50)
    tags: list[str] | None = None
    triggers: list[str] | None = None
    tools_required: list[str] | None = None
    activation_mode: str | None = None
    body: str | None = Field(None, min_length=1)
    metadata: dict[str, Any] | None = None
    attachments: list[dict[str, Any]] | None = None
    is_enabled: bool | None = None
    change_note: str | None = Field(None, max_length=500)

    @field_validator("activation_mode")
    @classmethod
    def _check_activation_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in ACTIVATION_MODES:
            raise ValueError(f"activation_mode must be one of {ACTIVATION_MODES}")
        return v


class SkillResponse(BaseModel):
    """Skill 详情响应（包含完整 body / metadata / attachments）。"""

    id: uuid.UUID
    project_id: uuid.UUID | None = None
    name: str
    slug: str
    description: str
    semantic_version: str
    category: str
    tags: list[Any] = Field(default_factory=list)
    triggers: list[Any] = Field(default_factory=list)
    tools_required: list[Any] = Field(default_factory=list)
    activation_mode: str
    body: str
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=_METADATA_ALIASES,
    )
    attachments: list[Any] = Field(default_factory=list)
    source: str
    source_url: str | None = None
    is_enabled: bool
    safety_scan_status: str
    safety_scan_notes: str | None = None
    db_version: int
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class SkillListResponse(BaseModel):
    """列表响应（不含 body / attachments，避免大字段传输）。"""

    id: uuid.UUID
    project_id: uuid.UUID | None = None
    name: str
    slug: str
    description: str
    semantic_version: str
    category: str
    tags: list[Any] = Field(default_factory=list)
    triggers: list[Any] = Field(default_factory=list)
    activation_mode: str
    source: str
    is_enabled: bool
    safety_scan_status: str
    db_version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ──────────────────────────────────────────────────────────────────────────────
# 版本 / 审计 / 安全扫描响应
# ──────────────────────────────────────────────────────────────────────────────


class SkillVersionResponse(BaseModel):
    id: uuid.UUID
    skill_id: uuid.UUID
    db_version: int
    body: str
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=_METADATA_ALIASES,
    )
    change_note: str | None = None
    created_by: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class SkillUsageLogResponse(BaseModel):
    id: uuid.UUID
    skill_id: uuid.UUID | None = None
    skill_db_version: int | None = None
    session_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    activation_reason: str
    matched_trigger: str | None = None
    tokens_consumed: int | None = None
    outcome: str
    error_message: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SkillSafetyScanResponse(BaseModel):
    id: uuid.UUID
    skill_id: uuid.UUID
    skill_db_version: int
    status: str
    findings: list[Any] = Field(default_factory=list)
    scanner_version: str
    scanned_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ──────────────────────────────────────────────────────────────────────────────
# 导入预览 / 触发词调试
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class SkillImportPreview:
    """ZIP 解包后给前端预览的数据结构（Task 12.3 才真正使用）。

    本 dataclass 在 Task 12.1 先定义类型契约；导入器 / 路由层未实装时不会被
    任何代码 import，因此放在 schemas 模块里安静等候即可。
    """

    name: str
    slug: str
    description: str
    semantic_version: str
    category: str
    activation_mode: str
    triggers: list[str] = field(default_factory=list)
    tools_required: list[str] = field(default_factory=list)
    body_preview: str = ""              # 前 N 字符截断
    body_size_bytes: int = 0
    attachments: list[dict[str, Any]] = field(default_factory=list)
    safety_status: str = "unscanned"    # clean | warning | blocked
    safety_findings: list[dict[str, Any]] = field(default_factory=list)
    metadata_extra_keys: list[str] = field(default_factory=list)  # YAML 前言里的未识别字段名
    skill_id: uuid.UUID | None = None  # blocked 时为 None


class MatchTriggerRequest(BaseModel):
    """Skill 详情页"召回测试"输入框使用（debug 用）。"""

    project_id: uuid.UUID
    message: str = Field(..., min_length=1, max_length=2000)
    max: int = Field(5, ge=1, le=20)


class SkillImportUrlRequest(BaseModel):
    """从 URL 导入 ZIP 或 SKILL.md 文本。"""

    url: str = Field(..., min_length=8, max_length=2000)
    ref: str | None = Field(None, max_length=200)


class SkillToggleRequest(BaseModel):
    """省略 ``is_enabled`` 时表示在当前状态下翻转。"""

    is_enabled: bool | None = None


class SkillManualChatActivateRequest(BaseModel):
    session_id: uuid.UUID
    manual_skill_ids: list[uuid.UUID] = Field(default_factory=list)


class SkillMatchTriggersJsonRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    max: int = Field(5, ge=1, le=20)


class MatchTriggerResponse(BaseModel):
    skill_id: uuid.UUID
    name: str
    slug: str
    score: int
    matched_triggers: list[str]


__all__ = [
    "ACTIVATION_MODES",
    "SOURCES",
    "SAFETY_STATUSES",
    "USAGE_REASONS",
    "USAGE_OUTCOMES",
    "SkillCreateRequest",
    "SkillUpdateRequest",
    "SkillResponse",
    "SkillListResponse",
    "SkillVersionResponse",
    "SkillUsageLogResponse",
    "SkillSafetyScanResponse",
    "SkillImportPreview",
    "SkillImportUrlRequest",
    "SkillToggleRequest",
    "SkillManualChatActivateRequest",
    "SkillMatchTriggersJsonRequest",
    "MatchTriggerRequest",
    "MatchTriggerResponse",
]

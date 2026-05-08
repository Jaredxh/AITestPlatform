import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class LLMConfig(Base):
    __tablename__ = "llm_configs"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    api_key_encrypted: Mapped[str | None] = mapped_column(String(500))
    base_url: Mapped[str | None] = mapped_column(String(500))
    temperature: Mapped[float] = mapped_column(Float, default=0.7, server_default="0.7")
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096, server_default="4096")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    creator = relationship("User", lazy="selectin")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=True)
    title: Mapped[str | None] = mapped_column(String(200))
    llm_config_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("llm_configs.id"), nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text)

    #: Phase 12 manual skill 选中等技术上下文（JSON：`manual_skill_ids` 等）
    chat_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    user = relationship("User", lazy="selectin")
    llm_config = relationship("LLMConfig", lazy="selectin")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", lazy="selectin",
        order_by="ChatMessage.created_at",
    )


#: Phase 13 / Task 13.0 — chat_messages.kind 枚举值。
#:
#: 历史/默认 ``normal``；新值由后续 task 写入：``skill_card`` 由 LLM 调
#: ``propose_execution_plan`` 后落库（task 13.3），``task_badge`` 由用户确认
#: 派发后落库（task 13.3），``execution_event`` 由 ``system_event_service`` 在
#: 执行完成时落库（本 task）。前端按 kind 分发到不同消息组件；未识别 kind
#: 兜底走 ``normal`` 渲染分支保持向前兼容。
CHAT_MESSAGE_KINDS: tuple[str, ...] = (
    "normal",
    "skill_card",
    "task_badge",
    "execution_event",
)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_used: Mapped[int | None] = mapped_column(Integer)
    model_used: Mapped[str | None] = mapped_column(String(100))
    meta_data: Mapped[dict | None] = mapped_column(JSON)
    #: Phase 12 / Task 12.6 — 该消息触发的 skill 调用日志 id（lazy load skill 时写入）
    skill_invocation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_usage_logs.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Phase 13 / Task 13.0 — 消息渲染类别。``normal`` 走老气泡分支，新枚举值
    #: 由 M1 task 13.3 / system_event_service 写入；老数据迁移 default 为
    #: ``'normal'``，select 不报错。
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="normal", server_default="normal",
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages")

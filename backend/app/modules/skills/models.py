"""Skill 模块 SQLAlchemy ORM 模型（Phase 12 / Task 12.1）。

数据模型对应设计文档 ``docs/PHASE3_DESIGN.md`` §四，共 4 张表：

- :class:`Skill`              — 技能元数据 + SKILL.md 正文 + 治理状态
- :class:`SkillVersion`       — 每次保存追加快照，便于回溯
- :class:`SkillUsageLog`      — 激活 / 调用审计（命中触发词、消耗 token、成败）
- :class:`SkillSafetyScan`    — 安全扫描记录（findings + status）

命名约定：

- DB 列名遵循设计文档（``metadata`` / ``triggers`` / ``attachments`` 等）
- ORM 属性 :attr:`Skill.extra_metadata` / :attr:`SkillVersion.extra_metadata`
  映射到 DB 列 ``metadata`` —— 因为 ``metadata`` 在 SQLAlchemy 2.x
  ``DeclarativeBase`` 上是保留的 class-level 属性，直接同名会触发 SAWarning
  并遮盖 ORM metadata 句柄；上层 Pydantic Schema 仍以 ``metadata`` 对外。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Skill(Base):
    """Skill 技能包（与 ``prompt_templates`` 完全独立的新表）。

    见 ``docs/PHASE3_DESIGN.md`` §4.1。
    """

    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", name="uq_skills_project_slug"),
        CheckConstraint(
            "activation_mode IN ("
            "'manual','trigger','agent_callable','always','auto_apply'"
            ")",
            name="ck_skills_activation_mode",
        ),
        CheckConstraint(
            "source IN ('built_in','imported','custom')",
            name="ck_skills_source",
        ),
        CheckConstraint(
            "safety_scan_status IN ('unscanned','clean','warning','blocked')",
            name="ck_skills_safety_scan_status",
        ),
        Index("idx_skills_project_enabled", "project_id", "is_enabled"),
        Index("idx_skills_activation_mode", "activation_mode"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    semantic_version: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'1.0.0'"),
    )
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'custom'"),
    )

    tags: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
        default=list,
    )
    triggers: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
        default=list,
    )
    tools_required: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
        default=list,
    )

    activation_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'agent_callable'"),
    )

    body: Mapped[str] = mapped_column(Text, nullable=False)
    extra_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict,
    )
    attachments: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
        default=list,
    )

    source: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'custom'"),
    )
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True,
    )

    safety_scan_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'unscanned'"),
    )
    safety_scan_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    db_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1"), default=1,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    project = relationship("Project", lazy="selectin")
    creator = relationship("User", lazy="selectin")
    versions: Mapped[list["SkillVersion"]] = relationship(
        back_populates="skill",
        cascade="all, delete-orphan",
        lazy="noload",
        order_by="SkillVersion.db_version.desc()",
    )


class SkillVersion(Base):
    """每次保存追加一行（与 :attr:`Skill.db_version` 一一对应）。

    见 ``docs/PHASE3_DESIGN.md`` §4.2。
    """

    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint(
            "skill_id", "db_version",
            name="uq_skill_versions_skill_dbver",
        ),
        Index("idx_skill_versions_skill", "skill_id", "db_version"),
    )

    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    db_version: Mapped[int] = mapped_column(Integer, nullable=False)

    body: Mapped[str] = mapped_column(Text, nullable=False)
    extra_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict,
    )
    change_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    skill: Mapped["Skill"] = relationship(back_populates="versions")
    creator = relationship("User", lazy="selectin")


class SkillUsageLog(Base):
    """Skill 激活 / 调用审计。

    覆盖 5 种 activation_reason（manual / trigger_match / agent_callable /
    always / auto_apply）。便于使用统计页 + 误召回排查。

    见 ``docs/PHASE3_DESIGN.md`` §4.3。
    """

    __tablename__ = "skill_usage_logs"
    __table_args__ = (
        CheckConstraint(
            "activation_reason IN ("
            "'manual','trigger_match','agent_callable','always','auto_apply'"
            ")",
            name="ck_skill_usage_activation_reason",
        ),
        CheckConstraint(
            "outcome IN ('success','failed','no_output','user_cancelled')",
            name="ck_skill_usage_outcome",
        ),
        Index("idx_skill_usage_skill_time", "skill_id", "created_at"),
        Index("idx_skill_usage_session", "session_id"),
    )

    skill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="SET NULL"),
        nullable=True,
    )
    skill_db_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="SET NULL"),
        nullable=True,
    )

    activation_reason: Mapped[str] = mapped_column(String(30), nullable=False)
    matched_trigger: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tokens_consumed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    outcome: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'success'"),
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class SkillSafetyScan(Base):
    """Skill 安全扫描记录。

    每次 import / 重新扫描时写一行；最新一行的 ``status`` 决定
    ``Skill.safety_scan_status``。

    见 ``docs/PHASE3_DESIGN.md`` §4.4。

    ``scanned_at`` 与 ``Base.created_at`` 在多数路径下取值相同（同步扫描时）；
    保留独立列是为了：将来异步重扫时可以解耦行创建时间与"扫描发生时间"。
    """

    __tablename__ = "skill_safety_scans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('clean','warning','blocked')",
            name="ck_skill_safety_status",
        ),
        Index("idx_skill_safety_skill", "skill_id", "scanned_at"),
    )

    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_db_version: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False)
    findings: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
        default=list,
    )
    scanner_version: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'1.0'"),
    )
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

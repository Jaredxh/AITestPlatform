"""add_skills_tables

三期 Phase 12 / Task 12.1：Skill 模块的 4 张表。

- ``skills``               — 技能元数据 + SKILL.md 正文 + 治理状态
- ``skill_versions``       — 每次保存追加快照
- ``skill_usage_logs``     — 激活 / 调用审计
- ``skill_safety_scans``   — 安全扫描记录（findings + status）

设计文档：``docs/PHASE3_DESIGN.md`` §四。

零侵入约束：
- 不修改 ``prompt_templates`` 等任何已有表
- 所有外键的 ondelete 与设计文档一致（CASCADE / SET NULL）

Revision ID: f7a8b9c0d1e2
Revises: f3a4b5c6d7e8
Create Date: 2026-05-07 09:55:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── skills ──────────────────────────────────────────────────────
    op.create_table(
        "skills",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "semantic_version", sa.String(length=20),
            server_default=sa.text("'1.0.0'"), nullable=False,
        ),
        sa.Column(
            "category", sa.String(length=50),
            server_default=sa.text("'custom'"), nullable=False,
        ),
        sa.Column(
            "tags", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"), nullable=False,
        ),
        sa.Column(
            "triggers", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"), nullable=False,
        ),
        sa.Column(
            "tools_required", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"), nullable=False,
        ),
        sa.Column(
            "activation_mode", sa.String(length=20),
            server_default=sa.text("'agent_callable'"), nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"), nullable=False,
        ),
        sa.Column(
            "attachments", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"), nullable=False,
        ),
        sa.Column(
            "source", sa.String(length=20),
            server_default=sa.text("'custom'"), nullable=False,
        ),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column(
            "is_enabled", sa.Boolean(),
            server_default=sa.text("true"), nullable=False,
        ),
        sa.Column(
            "safety_scan_status", sa.String(length=20),
            server_default=sa.text("'unscanned'"), nullable=False,
        ),
        sa.Column("safety_scan_notes", sa.Text(), nullable=True),
        sa.Column(
            "db_version", sa.Integer(),
            server_default=sa.text("1"), nullable=False,
        ),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.CheckConstraint(
            "activation_mode IN ("
            "'manual','trigger','agent_callable','always','auto_apply'"
            ")",
            name="ck_skills_activation_mode",
        ),
        sa.CheckConstraint(
            "source IN ('built_in','imported','custom')",
            name="ck_skills_source",
        ),
        sa.CheckConstraint(
            "safety_scan_status IN ("
            "'unscanned','clean','warning','blocked'"
            ")",
            name="ck_skills_safety_scan_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "slug", name="uq_skills_project_slug"),
    )
    op.create_index(op.f("ix_skills_id"), "skills", ["id"], unique=False)
    op.create_index(
        op.f("ix_skills_project_id"), "skills", ["project_id"], unique=False,
    )
    op.create_index(
        "idx_skills_project_enabled", "skills", ["project_id", "is_enabled"],
        unique=False,
    )
    op.create_index(
        "idx_skills_activation_mode", "skills", ["activation_mode"], unique=False,
    )

    # ─── skill_versions ──────────────────────────────────────────────
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("skill_id", sa.UUID(), nullable=False),
        sa.Column("db_version", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"), nullable=False,
        ),
        sa.Column("change_note", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["skills.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "skill_id", "db_version", name="uq_skill_versions_skill_dbver",
        ),
    )
    op.create_index(op.f("ix_skill_versions_id"), "skill_versions", ["id"], unique=False)
    op.create_index(
        op.f("ix_skill_versions_skill_id"), "skill_versions", ["skill_id"],
        unique=False,
    )
    op.create_index(
        "idx_skill_versions_skill", "skill_versions",
        ["skill_id", sa.text("db_version DESC")],
        unique=False,
    )

    # ─── skill_usage_logs ────────────────────────────────────────────
    op.create_table(
        "skill_usage_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("skill_id", sa.UUID(), nullable=True),
        sa.Column("skill_db_version", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("message_id", sa.UUID(), nullable=True),
        sa.Column("activation_reason", sa.String(length=30), nullable=False),
        sa.Column("matched_trigger", sa.String(length=200), nullable=True),
        sa.Column("tokens_consumed", sa.Integer(), nullable=True),
        sa.Column(
            "outcome", sa.String(length=20),
            server_default=sa.text("'success'"), nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["skills.id"], ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["chat_sessions.id"], ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["chat_messages.id"], ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "activation_reason IN ("
            "'manual','trigger_match','agent_callable','always','auto_apply'"
            ")",
            name="ck_skill_usage_activation_reason",
        ),
        sa.CheckConstraint(
            "outcome IN ('success','failed','no_output','user_cancelled')",
            name="ck_skill_usage_outcome",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_skill_usage_logs_id"), "skill_usage_logs", ["id"], unique=False,
    )
    op.create_index(
        "idx_skill_usage_skill_time", "skill_usage_logs",
        ["skill_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_skill_usage_session", "skill_usage_logs", ["session_id"], unique=False,
    )

    # ─── skill_safety_scans ──────────────────────────────────────────
    op.create_table(
        "skill_safety_scans",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("skill_id", sa.UUID(), nullable=False),
        sa.Column("skill_db_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "findings", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"), nullable=False,
        ),
        sa.Column(
            "scanner_version", sa.String(length=20),
            server_default=sa.text("'1.0'"), nullable=False,
        ),
        sa.Column(
            "scanned_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["skills.id"], ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('clean','warning','blocked')",
            name="ck_skill_safety_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_skill_safety_scans_id"), "skill_safety_scans", ["id"], unique=False,
    )
    op.create_index(
        "idx_skill_safety_skill", "skill_safety_scans",
        ["skill_id", sa.text("scanned_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_skill_safety_skill", table_name="skill_safety_scans")
    op.drop_index(op.f("ix_skill_safety_scans_id"), table_name="skill_safety_scans")
    op.drop_table("skill_safety_scans")

    op.drop_index("idx_skill_usage_session", table_name="skill_usage_logs")
    op.drop_index("idx_skill_usage_skill_time", table_name="skill_usage_logs")
    op.drop_index(op.f("ix_skill_usage_logs_id"), table_name="skill_usage_logs")
    op.drop_table("skill_usage_logs")

    op.drop_index("idx_skill_versions_skill", table_name="skill_versions")
    op.drop_index(op.f("ix_skill_versions_skill_id"), table_name="skill_versions")
    op.drop_index(op.f("ix_skill_versions_id"), table_name="skill_versions")
    op.drop_table("skill_versions")

    op.drop_index("idx_skills_activation_mode", table_name="skills")
    op.drop_index("idx_skills_project_enabled", table_name="skills")
    op.drop_index(op.f("ix_skills_project_id"), table_name="skills")
    op.drop_index(op.f("ix_skills_id"), table_name="skills")
    op.drop_table("skills")

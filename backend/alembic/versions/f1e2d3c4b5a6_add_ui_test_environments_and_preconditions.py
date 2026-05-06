"""add_ui_test_environments_and_preconditions

二期 Task 8.1：测试环境配置 + 前置步骤模板。

设计参考：docs/PHASE2_DESIGN.md §3.3.4（环境字段）+ §2.4（前置步骤 4 类型）。

Revision ID: f1e2d3c4b5a6
Revises: c3d5f7a9b1e2
Create Date: 2026-05-02 23:50:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f1e2d3c4b5a6"
down_revision: Union[str, None] = "c3d5f7a9b1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ui_test_environments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column(
            "allowed_hosts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("token_budget", sa.Integer(), server_default=sa.text("50000"), nullable=False),
        sa.Column(
            "enable_browser_evaluate",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("session_name", sa.String(length=100), nullable=True),
        sa.Column("state_saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "default_data_set_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("headless", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("viewport_width", sa.Integer(), server_default=sa.text("1280"), nullable=False),
        sa.Column("viewport_height", sa.Integer(), server_default=sa.text("800"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ui_test_environments_id"),
        "ui_test_environments", ["id"], unique=False,
    )
    op.create_index(
        op.f("ix_ui_test_environments_project_id"),
        "ui_test_environments", ["project_id"], unique=False,
    )

    op.create_table(
        "ui_precondition_templates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("environment_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("credentials_encrypted", sa.Text(), nullable=True),
        sa.Column("order_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("state_saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["environment_id"], ["ui_test_environments.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ui_precondition_templates_id"),
        "ui_precondition_templates", ["id"], unique=False,
    )
    op.create_index(
        op.f("ix_ui_precondition_templates_environment_id"),
        "ui_precondition_templates", ["environment_id"], unique=False,
    )

    # 让 type 字段在数据库层也加一道约束，防止误写值绕过 schemas 校验
    op.create_check_constraint(
        "ck_ui_precondition_template_type",
        "ui_precondition_templates",
        "type IN ('state_inject','ai_login','scripted_steps','cookie_inject')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_ui_precondition_template_type",
        "ui_precondition_templates", type_="check",
    )
    op.drop_index(
        op.f("ix_ui_precondition_templates_environment_id"),
        table_name="ui_precondition_templates",
    )
    op.drop_index(
        op.f("ix_ui_precondition_templates_id"),
        table_name="ui_precondition_templates",
    )
    op.drop_table("ui_precondition_templates")

    op.drop_index(
        op.f("ix_ui_test_environments_project_id"),
        table_name="ui_test_environments",
    )
    op.drop_index(
        op.f("ix_ui_test_environments_id"),
        table_name="ui_test_environments",
    )
    op.drop_table("ui_test_environments")

"""add_ui_execution_tables

二期 Task 9.5：执行结果三张表。

- ``ui_executions``：单次执行批次
- ``ui_case_results``：单条用例结果（含 data_confidence / synthesized_data /
  data_failures，与 status 正交，用于业务质量统计）
- ``ui_step_results``：每步执行记录（tool_calls / snapshot / assertion）

设计文档：docs/PHASE2_DESIGN.md §4.1。

Revision ID: d3c8a2b6f410
Revises: a7b2c8d1e4f5
Create Date: 2026-05-03 19:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d3c8a2b6f410"
down_revision: Union[str, None] = "a7b2c8d1e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── ui_executions ───────────────────────────────────────────────
    op.create_table(
        "ui_executions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("environment_id", sa.UUID(), nullable=True),
        sa.Column(
            "status", sa.String(length=20),
            server_default=sa.text("'pending'"), nullable=False,
        ),
        sa.Column(
            "mode", sa.String(length=20),
            server_default=sa.text("'normal'"), nullable=False,
        ),
        sa.Column("total_cases", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("passed_cases", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed_cases", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("skipped_cases", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("video_path", sa.String(length=500), nullable=True),
        sa.Column("trace_path", sa.String(length=500), nullable=True),
        sa.Column("chat_message_id", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_by", sa.UUID(), nullable=True),
        sa.Column(
            "test_data_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "config_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["environment_id"], ["ui_test_environments.id"], ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["chat_message_id"], ["chat_messages.id"], ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ui_executions_id"), "ui_executions", ["id"], unique=False)
    op.create_index(
        op.f("ix_ui_executions_project_id"),
        "ui_executions", ["project_id"], unique=False,
    )
    op.create_index(
        "idx_ui_executions_project_status",
        "ui_executions",
        ["project_id", "status", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_check_constraint(
        "ck_ui_executions_status",
        "ui_executions",
        "status IN ('pending','running','completed','stopped','failed','aborted_budget')",
    )
    op.create_check_constraint(
        "ck_ui_executions_mode",
        "ui_executions",
        "mode IN ('normal','debug')",
    )

    # ─── ui_case_results ─────────────────────────────────────────────
    op.create_table(
        "ui_case_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("execution_id", sa.UUID(), nullable=False),
        sa.Column("testcase_id", sa.UUID(), nullable=True),
        sa.Column(
            "status", sa.String(length=20),
            server_default=sa.text("'pending'"), nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "test_data_used",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "synthesized_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "data_failures",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "data_confidence", sa.String(length=20),
            server_default=sa.text("'reliable'"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["ui_executions.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["testcase_id"], ["testcases.id"], ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ui_case_results_id"),
        "ui_case_results", ["id"], unique=False,
    )
    op.create_index(
        op.f("ix_ui_case_results_execution_id"),
        "ui_case_results", ["execution_id"], unique=False,
    )
    op.create_index(
        "idx_ui_case_results_confidence",
        "ui_case_results", ["data_confidence"], unique=False,
    )
    op.create_check_constraint(
        "ck_ui_case_results_status",
        "ui_case_results",
        "status IN ('pending','running','passed','failed','error','skipped')",
    )
    op.create_check_constraint(
        "ck_ui_case_results_data_confidence",
        "ui_case_results",
        "data_confidence IN ('reliable','synthesized','data_failure')",
    )

    # ─── ui_step_results ─────────────────────────────────────────────
    op.create_table(
        "ui_step_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("case_result_id", sa.UUID(), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("expected_result", sa.Text(), nullable=True),
        sa.Column(
            "tool_calls",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("ai_reasoning", sa.Text(), nullable=True),
        sa.Column("snapshot_before", sa.Text(), nullable=True),
        sa.Column("snapshot_after", sa.Text(), nullable=True),
        sa.Column("assertion_passed", sa.Boolean(), nullable=True),
        sa.Column("assertion_reason", sa.Text(), nullable=True),
        sa.Column("assertion_evidence", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=20),
            server_default=sa.text("'pending'"), nullable=False,
        ),
        sa.Column("screenshot_path", sa.String(length=500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_result_id"], ["ui_case_results.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ui_step_results_id"),
        "ui_step_results", ["id"], unique=False,
    )
    op.create_index(
        op.f("ix_ui_step_results_case_result_id"),
        "ui_step_results", ["case_result_id"], unique=False,
    )
    op.create_index(
        "idx_ui_step_results_case",
        "ui_step_results", ["case_result_id", "step_number"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_ui_step_results_status",
        "ui_step_results",
        "status IN ('pending','running','passed','failed','skipped','blocked_by_security')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ui_step_results_status", "ui_step_results", type_="check")
    op.drop_index("idx_ui_step_results_case", table_name="ui_step_results")
    op.drop_index(op.f("ix_ui_step_results_case_result_id"), table_name="ui_step_results")
    op.drop_index(op.f("ix_ui_step_results_id"), table_name="ui_step_results")
    op.drop_table("ui_step_results")

    op.drop_constraint("ck_ui_case_results_data_confidence", "ui_case_results", type_="check")
    op.drop_constraint("ck_ui_case_results_status", "ui_case_results", type_="check")
    op.drop_index("idx_ui_case_results_confidence", table_name="ui_case_results")
    op.drop_index(op.f("ix_ui_case_results_execution_id"), table_name="ui_case_results")
    op.drop_index(op.f("ix_ui_case_results_id"), table_name="ui_case_results")
    op.drop_table("ui_case_results")

    op.drop_constraint("ck_ui_executions_mode", "ui_executions", type_="check")
    op.drop_constraint("ck_ui_executions_status", "ui_executions", type_="check")
    op.drop_index("idx_ui_executions_project_status", table_name="ui_executions")
    op.drop_index(op.f("ix_ui_executions_project_id"), table_name="ui_executions")
    op.drop_index(op.f("ix_ui_executions_id"), table_name="ui_executions")
    op.drop_table("ui_executions")

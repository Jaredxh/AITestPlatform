"""add_test_data_sets_and_items + testcase default data set ids

二期 Task 8.5：测试物料（test_data_sets / test_data_items）。

- 建两张新表，带 CHECK 约束保证 scope / value_type 枚举一致性。
- 给 ``testcases`` 加 ``default_data_set_ids`` JSONB 列（用例级物料默认绑定）；
  环境级绑定已由 Task 8.1 在 ``ui_test_environments`` 上建好。
- ``ui_executions`` / ``ui_case_results`` 表本 task 还未建（Task 9.5 才建），
  所以设计文档中提到的 "snapshot 字段" 在此不做。

Revision ID: a7b2c8d1e4f5
Revises: f1e2d3c4b5a6
Create Date: 2026-05-03 10:10:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7b2c8d1e4f5"
down_revision: Union[str, None] = "f1e2d3c4b5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── test_data_sets ──────────────────────────────────────────────
    op.create_table(
        "test_data_sets",
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
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=True),
        sa.Column(
            "scope", sa.String(length=20),
            server_default=sa.text("'project'"), nullable=False,
        ),
        sa.Column("environment_id", sa.UUID(), nullable=True),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column(
            "is_default", sa.Boolean(),
            server_default=sa.text("false"), nullable=False,
        ),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["environment_id"], ["ui_test_environments.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_test_data_sets_id"),
        "test_data_sets", ["id"], unique=False,
    )
    op.create_index(
        op.f("ix_test_data_sets_project_id"),
        "test_data_sets", ["project_id"], unique=False,
    )
    op.create_index(
        op.f("ix_test_data_sets_environment_id"),
        "test_data_sets", ["environment_id"], unique=False,
    )
    op.create_index(
        op.f("ix_test_data_sets_owner_id"),
        "test_data_sets", ["owner_id"], unique=False,
    )
    op.create_check_constraint(
        "ck_test_data_sets_scope",
        "test_data_sets",
        "scope IN ('project','environment','personal')",
    )

    # ─── test_data_items ─────────────────────────────────────────────
    op.create_table(
        "test_data_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("set_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value_type", sa.String(length=20), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "value_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_mime", sa.String(length=100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(),
            server_default=sa.text("0"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["set_id"], ["test_data_sets.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("set_id", "key", name="uq_test_data_items_set_key"),
    )
    op.create_index(
        op.f("ix_test_data_items_id"),
        "test_data_items", ["id"], unique=False,
    )
    op.create_index(
        op.f("ix_test_data_items_set_id"),
        "test_data_items", ["set_id"], unique=False,
    )
    op.create_check_constraint(
        "ck_test_data_items_value_type",
        "test_data_items",
        "value_type IN ('string','secret','multiline','file','random','dataset')",
    )

    # ─── 给 testcases 加 default_data_set_ids ─────────────────────────
    # 环境已经由 Task 8.1 建好 default_data_set_ids 字段，这里只补 testcase。
    op.add_column(
        "testcases",
        sa.Column(
            "default_data_set_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("testcases", "default_data_set_ids")

    op.drop_constraint(
        "ck_test_data_items_value_type",
        "test_data_items", type_="check",
    )
    op.drop_index(
        op.f("ix_test_data_items_set_id"),
        table_name="test_data_items",
    )
    op.drop_index(
        op.f("ix_test_data_items_id"),
        table_name="test_data_items",
    )
    op.drop_table("test_data_items")

    op.drop_constraint(
        "ck_test_data_sets_scope",
        "test_data_sets", type_="check",
    )
    op.drop_index(
        op.f("ix_test_data_sets_owner_id"),
        table_name="test_data_sets",
    )
    op.drop_index(
        op.f("ix_test_data_sets_environment_id"),
        table_name="test_data_sets",
    )
    op.drop_index(
        op.f("ix_test_data_sets_project_id"),
        table_name="test_data_sets",
    )
    op.drop_index(
        op.f("ix_test_data_sets_id"),
        table_name="test_data_sets",
    )
    op.drop_table("test_data_sets")

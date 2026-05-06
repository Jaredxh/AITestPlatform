"""add_module_id_to_ai_generation_batches

Revision ID: b2c4e6a8d0f1
Revises: a1b2c3d4e5f6
Create Date: 2026-05-02 09:30:00.000000

添加 module_id 列到 ai_generation_batches：AI 生成用例时记住目标模块，
避免页面刷新或后台恢复任务后丢失模块上下文，导致最终入库到"未分类"。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c4e6a8d0f1"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_generation_batches",
        sa.Column("module_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_ai_generation_batches_module_id",
        "ai_generation_batches",
        "testcase_modules",
        ["module_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_ai_generation_batches_module_id",
        "ai_generation_batches",
        ["module_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_generation_batches_module_id",
        table_name="ai_generation_batches",
    )
    op.drop_constraint(
        "fk_ai_generation_batches_module_id",
        "ai_generation_batches",
        type_="foreignkey",
    )
    op.drop_column("ai_generation_batches", "module_id")

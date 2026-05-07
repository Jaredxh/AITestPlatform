"""add chat_messages.skill_invocation_id (Phase 12 / Task 12.6).

模型在某轮对话里调用了 ``skill_*__invoke`` 工具时，会写一条 SkillUsageLog；
这个字段把"该消息触发的 skill 调用"指回那条 log，前端据此渲染 SkillUsageBadge。

外键 ``ondelete=SET NULL``：删 SkillUsageLog 时不连带删消息，仅清空指针，避免
统计页清理历史时把对话列表打穿。

Revision ID: b3f8e9a1c5d2
Revises: a1b2c3d4e5f8
Create Date: 2026-05-07 13:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b3f8e9a1c5d2"
down_revision: Union[str, None] = "a1b2c3d4e5f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("skill_invocation_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_chat_messages_skill_invocation",
        source_table="chat_messages",
        referent_table="skill_usage_logs",
        local_cols=["skill_invocation_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_chat_messages_skill_invocation",
        "chat_messages",
        ["skill_invocation_id"],
        unique=False,
        postgresql_where=sa.text("skill_invocation_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_chat_messages_skill_invocation", table_name="chat_messages")
    op.drop_constraint(
        "fk_chat_messages_skill_invocation",
        "chat_messages",
        type_="foreignkey",
    )
    op.drop_column("chat_messages", "skill_invocation_id")

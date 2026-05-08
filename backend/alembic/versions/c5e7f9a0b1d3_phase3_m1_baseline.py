"""phase 3 / M1 baseline — chat_messages.kind + ui_executions 派发字段（Task 13.0）。

本次迁移把 Phase 3·M1 数据模型的"全可选基线"加进 DB；零回归承诺：

- ``chat_messages.kind``：默认 ``'normal'``，老消息回看走原渲染分支；新增枚举值
  ``skill_card / task_badge / execution_event`` 由 M1 后续 task 13.3 写入。
- ``ui_executions.source``：默认 ``'catalog'`` —— 二期"用例管理 → 执行 UI 自动化"
  入口仍走 catalog；只有 chat 派发的执行才会写 ``'chat'``，M2 即席用例会写
  ``'adhoc'``。统计页默认按 ``source='catalog'`` 过滤防 adhoc 污染通过率。
- ``ui_executions.triggered_chat_session_id``：仅 chat 派发场景填充；用于
  ``system_event_service.publish_execution_done()`` 完成时把 ``execution_event``
  消息回流到正确会话。带条件部分索引（``WHERE NOT NULL``），不影响主路径写入成本。
- ``ui_executions.adhoc_steps``：M1 仅占位（始终 NULL）；M2 task 13.6 接通即席
  用例后写入草拟步骤快照。

设计文档曾写表名 ``ui_execution_tasks``——实际二期落地命名为 ``ui_executions``，
本次迁移以**实际表名**为准。

Revision ID: c5e7f9a0b1d3
Revises: b3f8e9a1c5d2
Create Date: 2026-05-07 17:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c5e7f9a0b1d3"
down_revision: Union[str, None] = "b3f8e9a1c5d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── chat_messages.kind ─────────────────────────────────────────
    # 显式带 server_default 让历史行原地回填为 ``'normal'``；之后保留
    # server_default 给"未声明 kind"的新写入路径继续走 normal（与 ORM 默认值
    # 互为兜底，避免 INSERT 时漏写报 NOT NULL 异常）。
    op.add_column(
        "chat_messages",
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'normal'"),
        ),
    )

    # ── ui_executions: source / triggered_chat_session_id / adhoc_steps ──
    op.add_column(
        "ui_executions",
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'catalog'"),
        ),
    )
    op.add_column(
        "ui_executions",
        sa.Column(
            "triggered_chat_session_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_ui_executions_triggered_chat_session",
        source_table="ui_executions",
        referent_table="chat_sessions",
        local_cols=["triggered_chat_session_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_ui_executions_triggered_chat_session",
        "ui_executions",
        ["triggered_chat_session_id"],
        unique=False,
        postgresql_where=sa.text("triggered_chat_session_id IS NOT NULL"),
    )

    op.add_column(
        "ui_executions",
        sa.Column("adhoc_steps", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ui_executions", "adhoc_steps")

    op.drop_index(
        "idx_ui_executions_triggered_chat_session", table_name="ui_executions"
    )
    op.drop_constraint(
        "fk_ui_executions_triggered_chat_session",
        "ui_executions",
        type_="foreignkey",
    )
    op.drop_column("ui_executions", "triggered_chat_session_id")
    op.drop_column("ui_executions", "source")

    op.drop_column("chat_messages", "kind")

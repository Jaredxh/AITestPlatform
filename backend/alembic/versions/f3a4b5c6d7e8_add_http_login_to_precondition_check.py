"""add_http_login_to_precondition_check

Revision ID: f3a4b5c6d7e8
Revises: e1f2a3b4c5d6
Create Date: 2026-05-05 00:55:00.000000

Task 8.2.5：把 ``http_login`` 加进 ``ui_precondition_templates.type`` 的
CHECK 约束。

为什么不能漏 DB 层的约束更新：``models.PRECONDITION_TYPES`` /
``schemas.PRECONDITION_TYPE_PATTERN`` 加 http_login 后，应用层会接受新值，
但 INSERT 到 PG 时被原 ``ck_ui_precondition_template_type`` 约束拒绝
（CheckViolationError），表现为创建前置步骤接口直接 500。

实现：DROP 旧约束 → 重新 ADD 含 http_login 的新约束。约束名复用，避免迁移
链上反复改名。
"""
from typing import Sequence, Union

from alembic import op


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_TYPES = "'state_inject','ai_login','scripted_steps','cookie_inject'"
_NEW_TYPES = (
    "'state_inject','ai_login','scripted_steps','cookie_inject','http_login'"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_ui_precondition_template_type",
        "ui_precondition_templates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ui_precondition_template_type",
        "ui_precondition_templates",
        f"type IN ({_NEW_TYPES})",
    )


def downgrade() -> None:
    # 回滚前需要确认表里没有 type='http_login' 的行 —— 否则 ADD 旧约束会失败。
    # 这里刻意 *不* 自动删数据，保留让运维显式处理：
    #   DELETE FROM ui_precondition_templates WHERE type='http_login';
    # 再跑 alembic downgrade。
    op.drop_constraint(
        "ck_ui_precondition_template_type",
        "ui_precondition_templates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ui_precondition_template_type",
        "ui_precondition_templates",
        f"type IN ({_OLD_TYPES})",
    )

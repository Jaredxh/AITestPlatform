"""add_entry_path_to_testcase_modules

Revision ID: e1f2a3b4c5d6
Revises: d3c8a2b6f410
Create Date: 2026-05-04 20:30:00.000000

为 testcase_modules 增加 ``entry_path`` 字段，记录"该模块下用例执行时
应该 navigate 的入口路径"。配合 TestEnvironment.base_url 使用，解决
"同一系统多子模块、每个模块入口不同"的常见场景，避免每个模块都重复
建一份环境配置 / 登录前置。

接受三种值：
- 绝对路径："/admin/users"
- 完整 URL："https://other.example.com/x"
- NULL：未配置 → 行为退回到现状（由用例 step 自然语言决定）

完全可空，老数据无需迁移。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d3c8a2b6f410"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "testcase_modules",
        sa.Column("entry_path", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("testcase_modules", "entry_path")

"""phase 3 / Task 13.2 — testcases.tags JSONB（可选；GIN 索引）。

把 ``tags`` 增量加到 ``testcases`` 表，给 ``ui_automation skill`` 的
``case_matcher`` 在策略 2（title / tags 全文检索）使用：用户说 "回归用例" /
"P0 用例" / "登录相关"时，能基于 tags 快速召回。

零回归承诺：
- 字段全部可选，``server_default '[]'::jsonb`` 让历史行原地回填空数组；
  老数据 ``select`` 不报错（DoD 第 5 条）。
- GIN 索引仅加速 ``tags ? 'xxx'`` / ``tags @> '["xxx"]'`` 查询；其它路径不
  受影响。

Revision ID: d9b1c2e4f5a6
Revises: c5e7f9a0b1d3
Create Date: 2026-05-07 18:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d9b1c2e4f5a6"
down_revision: Union[str, None] = "c5e7f9a0b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "testcases",
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_index(
        "idx_testcases_tags_gin",
        "testcases",
        ["tags"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("idx_testcases_tags_gin", table_name="testcases")
    op.drop_column("testcases", "tags")

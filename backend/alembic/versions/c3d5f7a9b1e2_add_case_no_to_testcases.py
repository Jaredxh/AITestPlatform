"""add_case_no_to_testcases

Revision ID: c3d5f7a9b1e2
Revises: b2c4e6a8d0f1
Create Date: 2026-05-02 00:00:00.000000

为 testcases 增加项目内自增的"用例编号" case_no，并对 (project_id, case_no)
建唯一索引，给前端展示成 TC-xxxx 这种好看且稳定的人类编号。

升级流程：
1. 先用默认值 0 加列，避免锁表 + 不阻塞已有插入；
2. 用窗口函数 ROW_NUMBER() 按 (project_id, created_at, id) 顺序回填，
   保证同一项目内编号从 1 起严格递增；
3. 把 server_default 去掉（后续插入由应用层显式分配，不再用 0）；
4. 建复合唯一索引 ix_testcases_project_case_no_uniq。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d5f7a9b1e2"
down_revision: Union[str, None] = "b2c4e6a8d0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "testcases",
        sa.Column(
            "case_no",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    op.execute(
        """
        WITH numbered AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY project_id
                       ORDER BY created_at, id
                   ) AS rn
            FROM testcases
        )
        UPDATE testcases t
        SET case_no = numbered.rn
        FROM numbered
        WHERE t.id = numbered.id
        """
    )

    op.alter_column(
        "testcases",
        "case_no",
        server_default=None,
    )

    op.create_index(
        "ix_testcases_project_case_no_uniq",
        "testcases",
        ["project_id", "case_no"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_testcases_project_case_no_uniq", table_name="testcases")
    op.drop_column("testcases", "case_no")

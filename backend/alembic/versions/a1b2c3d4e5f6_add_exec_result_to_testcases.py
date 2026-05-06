"""add_exec_result_to_testcases

Revision ID: a1b2c3d4e5f6
Revises: d80326b76bad
Create Date: 2026-04-30 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "d80326b76bad"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "testcases",
        sa.Column(
            "exec_result",
            sa.String(length=20),
            server_default="not_run",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("testcases", "exec_result")

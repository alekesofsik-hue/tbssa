"""make user telegram_id nullable

Revision ID: 9f3e7c1a2b4d
Revises: 6d2c4e7f9a1b
Create Date: 2026-04-18 19:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9f3e7c1a2b4d"
down_revision: Union[str, Sequence[str], None] = "6d2c4e7f9a1b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "telegram_id",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade() -> None:
    op.execute("DELETE FROM users WHERE telegram_id IS NULL")
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "telegram_id",
            existing_type=sa.Integer(),
            nullable=False,
        )

"""add max user support

Revision ID: 6d2c4e7f9a1b
Revises: c8f2d1e4a3b0
Create Date: 2026-02-19 23:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "6d2c4e7f9a1b"
down_revision: Union[str, Sequence[str], None] = "c8f2d1e4a3b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("max_user_id", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("max_username", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("max_first_name", sa.String(length=128), nullable=True))
    op.create_index(op.f("ix_users_max_user_id"), "users", ["max_user_id"], unique=True)

    op.add_column(
        "audit_log",
        sa.Column(
            "platform",
            sa.String(length=16),
            nullable=False,
            server_default="telegram",
        ),
    )


def downgrade() -> None:
    op.drop_column("audit_log", "platform")

    op.drop_index(op.f("ix_users_max_user_id"), table_name="users")
    op.drop_column("users", "max_first_name")
    op.drop_column("users", "max_username")
    op.drop_column("users", "max_user_id")

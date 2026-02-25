"""add server ping status

Revision ID: b9e1a3f2c7d0
Revises: 33d446001341
Create Date: 2026-02-19 20:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9e1a3f2c7d0"
down_revision: Union[str, Sequence[str], None] = "33d446001341"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("servers", sa.Column("last_ping_ok", sa.Boolean(), nullable=True))
    op.add_column("servers", sa.Column("last_ping_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("servers", "last_ping_at")
    op.drop_column("servers", "last_ping_ok")

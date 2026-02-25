"""add user display_name

Revision ID: c8f2d1e4a3b0
Revises: b9e1a3f2c7d0
Create Date: 2026-02-19 22:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8f2d1e4a3b0"
down_revision: Union[str, Sequence[str], None] = "b9e1a3f2c7d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "display_name")

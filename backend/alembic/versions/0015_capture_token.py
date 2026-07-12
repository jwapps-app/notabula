"""Revocable capture-only token (users.capture_token_hash).

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("capture_token_hash", sa.String(length=64), nullable=True)
    )
    op.create_index(
        "ix_users_capture_token_hash", "users", ["capture_token_hash"]
    )


def downgrade() -> None:
    op.drop_index("ix_users_capture_token_hash", table_name="users")
    op.drop_column("users", "capture_token_hash")

"""Locked notes: client-side-encrypted body.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notes",
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("notes", sa.Column("cipher_body", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("notes", "cipher_body")
    op.drop_column("notes", "locked")

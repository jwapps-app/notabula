"""TOTP replay guard: users.totp_last_counter.

The time-step of the last accepted authenticator code. Codes at or before
it are refused, making each code single-use within its validity window.

Revision ID: 0018
Revises: 0017
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_last_counter", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "totp_last_counter")

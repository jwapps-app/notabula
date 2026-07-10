"""Push targets (APNs devices, Web Push subscriptions) + note reminders.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.models.types import GUID

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "user_id",
            GUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("token", sa.String(length=200), nullable=False, unique=True),
        sa.Column("sandbox", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "push_subscriptions",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "user_id",
            GUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("endpoint", sa.String(length=1024), nullable=False, unique=True),
        sa.Column("p256dh", sa.String(length=200), nullable=False),
        sa.Column("auth", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "notes", sa.Column("remind_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "notes", sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_notes_remind_at", "notes", ["remind_at"])


def downgrade() -> None:
    op.drop_index("ix_notes_remind_at", table_name="notes")
    op.drop_column("notes", "reminded_at")
    op.drop_column("notes", "remind_at")
    op.drop_table("push_subscriptions")
    op.drop_table("devices")

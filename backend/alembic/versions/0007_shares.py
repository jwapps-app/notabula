"""Sharing: note_shares + folder_shares tables.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _share_table(name: str, target_col: str, target_table: str) -> None:
    op.create_table(
        name,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(target_col, UUID(as_uuid=True), sa.ForeignKey(f"{target_table}.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(target_col, "user_id", name=f"uq_{name[:-1]}"),
    )
    op.create_index(f"ix_{name}_{target_col}", name, [target_col])
    op.create_index(f"ix_{name}_user_id", name, ["user_id"])


def upgrade() -> None:
    _share_table("note_shares", "note_id", "notes")
    _share_table("folder_shares", "folder_id", "folders")


def downgrade() -> None:
    op.drop_table("folder_shares")
    op.drop_table("note_shares")

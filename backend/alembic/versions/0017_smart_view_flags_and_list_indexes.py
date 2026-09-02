"""Audit follow-ups: has_open_tasks flag; composite list indexes.

- notes.has_open_tasks: the "Open Tasks" smart view LIKE-scanned every
  body cast to text on each request. Now a boolean the ORM keeps in step
  on every body write (like `thumb`), backfilled here for existing rows.
- Composite partial indexes for the two hottest list queries
  (owner/folder + live-only + the pinned/updated ordering). Until now only
  single-column indexes existed, so the sort ran on every list call.

Revision ID: 0017
Revises: 0016
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notes",
        sa.Column(
            "has_open_tasks", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    # Same test the old smart view used; JSONB text output uses ": " but a
    # row written by the SQLite-style path could be compact, so match both.
    # (`\:` — a bare colon would be read by text() as a bind parameter.)
    op.execute(
        sa.text(
            "UPDATE notes SET has_open_tasks = true "
            "WHERE body IS NOT NULL AND ("
            "body\\:\\:text LIKE '%\"checked\"\\: false%' "
            "OR body\\:\\:text LIKE '%\"checked\"\\:false%')"
        )
    )
    op.create_index(
        "ix_notes_owner_live",
        "notes",
        ["owner_id", sa.text("pinned DESC"), sa.text("updated_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_notes_folder_live",
        "notes",
        ["folder_id", sa.text("pinned DESC"), sa.text("updated_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_notes_folder_live", table_name="notes")
    op.drop_index("ix_notes_owner_live", table_name="notes")
    op.drop_column("notes", "has_open_tasks")

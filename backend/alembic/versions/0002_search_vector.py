"""Full-text search: generated tsvector column + GIN index on notes.

Title is weighted above body so title hits rank first. The column is
GENERATED ALWAYS ... STORED, so it maintains itself on every write — no
trigger and no ORM involvement (the model deliberately omits it; tests
run on SQLite where the /search endpoint falls back to LIKE).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE notes ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(body_text, '')), 'B')
        ) STORED
        """
    )
    op.create_index(
        "ix_notes_search_vector",
        "notes",
        ["search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_notes_search_vector", table_name="notes")
    op.execute("ALTER TABLE notes DROP COLUMN search_vector")

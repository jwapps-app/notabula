"""Audit follow-ups: tag_id index; hash-unique link-preview URLs.

- note_tags gets an index on tag_id: the composite PK leads with note_id,
  so tag-first queries (tag listing counts, rename, orphan sweep) scanned.
- link_previews.url was UNIQUE via a plain btree on a 2048-char column; a
  long multibyte URL can exceed Postgres's btree row limit and error on
  insert. Uniqueness moves to an md5(url) expression index (lookups go
  through md5(url) = md5(:url) so they stay indexed).

Revision ID: 0016
Revises: 0015
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_note_tags_tag_id", "note_tags", ["tag_id"])
    op.drop_constraint("uq_link_previews_url", "link_previews", type_="unique")
    op.create_index(
        "uq_link_previews_url_md5",
        "link_previews",
        [sa.text("md5(url)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_link_previews_url_md5", table_name="link_previews")
    op.create_unique_constraint("uq_link_previews_url", "link_previews", ["url"])
    op.drop_index("ix_note_tags_tag_id", table_name="note_tags")

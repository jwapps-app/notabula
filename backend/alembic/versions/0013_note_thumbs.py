"""Gallery thumbnails: notes.thumb = first image URL in the body.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-09

"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _first_image_src(body) -> str | None:
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return None
    if not isinstance(body, dict):
        return None

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "image":
                src = (node.get("attrs") or {}).get("src")
                if isinstance(src, str) and src:
                    return src[:2048]
            for child in node.get("content") or []:
                found = walk(child)
                if found:
                    return found
        return None

    return walk(body)


def upgrade() -> None:
    op.add_column("notes", sa.Column("thumb", sa.String(length=2048), nullable=True))

    # Backfill existing notes so the gallery is populated on day one.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, body FROM notes WHERE body IS NOT NULL")
    ).fetchall()
    for note_id, body in rows:
        src = _first_image_src(body)
        if src:
            conn.execute(
                sa.text("UPDATE notes SET thumb = :src WHERE id = :id"),
                {"src": src, "id": note_id},
            )


def downgrade() -> None:
    op.drop_column("notes", "thumb")

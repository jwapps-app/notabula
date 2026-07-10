"""Notes — the core entity.

Bodies are stored as ProseMirror JSON (the TipTap document model) plus a
plain-text extraction used for list previews now and full-text search in
Phase 2. Deletes are soft (deleted_at) to power a "Recently Deleted"
folder like iOS Notes.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column

from sqlalchemy.orm import relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.tag import Tag, note_tags
from app.models.types import GUID, JSONDoc


def first_image_src(body: dict | None) -> str | None:
    """First image URL in a ProseMirror doc — the note's gallery thumbnail."""

    def walk(node) -> str | None:
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

    return walk(body) if body else None


class Note(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notes"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    folder_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("folders.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Derived from the first line of the body, like Apple Notes — there is
    # no separate title field in the editor.
    title: Mapped[str] = mapped_column(String(400), default="", nullable=False)
    # ProseMirror document JSON.
    body: Mapped[dict | None] = mapped_column(JSONDoc(), nullable=True)
    # Plain-text extraction: list previews now, tsvector source in Phase 2.
    body_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # First image URL in the body — the gallery-view thumbnail. A real
    # column (maintained by the body "set" listener below) so list queries
    # can keep deferring the heavy body JSON.
    thumb: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Locked notes: the client encrypts the body with a passphrase the
    # server never sees; body/body_text are cleared while locked (so search,
    # tags, and previews reveal nothing) and cipher_body holds the blob.
    # The title stays in plaintext, like iOS locked notes.
    locked: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    cipher_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optimistic-concurrency counter: updates must send the version they were
    # based on; stale writes are rejected (409) instead of clobbering.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Soft delete → "Recently Deleted"; purged after 30 days.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Per-note reminder: push the owner at remind_at; reminded_at records
    # the firing (cleared whenever remind_at is changed).
    remind_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Synced from #hashtags in body_text on every save. Query-only: rows in
    # note_tags are written directly by services/tags.py (async engines can't
    # lazy-load), so this exists for filter expressions like Note.tags.any().
    tags: Mapped[list[Tag]] = relationship(
        secondary=note_tags, lazy="raise", viewonly=True
    )


@event.listens_for(Note.body, "set")
def _sync_thumb(target: Note, value, _oldvalue, _initiator) -> None:
    """Every body write (create kwargs, updates, imports, guest edits,
    locking's body=None) keeps the thumbnail in step — no write path can
    forget it."""
    target.thumb = first_image_src(value)

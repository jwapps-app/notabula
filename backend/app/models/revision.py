"""Note revisions — the edit history behind the redline view.

One row per *editing session*: consecutive saves by the same user within
a short window collapse into one revision (autosave fires every ~700ms,
which would otherwise produce noise). Each revision stores the note's
state AFTER that session, so the change a session made is the diff
against the previous revision.
"""

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import GUID, JSONDoc


class NoteRevision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "note_revisions"

    note_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("notes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL so history survives a deleted account (shows as "someone").
    editor_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Set instead of editor_id when an anonymous secret-link visitor edits:
    # the name they gave themselves, so history reads "Sue (guest)".
    guest_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # The note's version at the end of this editing session.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(400), default="", nullable=False)
    body: Mapped[dict | None] = mapped_column(JSONDoc(), nullable=True)
    body_text: Mapped[str] = mapped_column(Text, default="", nullable=False)

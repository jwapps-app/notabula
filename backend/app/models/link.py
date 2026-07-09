"""Public links — "anyone with the link", no account needed.

One link per note, owner-created, revocable (delete the row), with a role:
viewers read, editors edit (guest edits land in the note's history, so the
redline + restore make them safe). The token is stored as-is: unlike
session tokens, it grants access to content that lives in plaintext in the
same database, so hashing it would only stop the owner from re-copying
the link without protecting anything extra.
"""

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import GUID


class NoteLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "note_links"

    note_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("notes.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # viewer|editor

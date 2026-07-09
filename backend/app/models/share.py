"""Sharing grants — a note or folder shared with another user.

Roles: "viewer" (read) or "editor" (read + edit content). Owners implicitly
hold every permission and never appear in these tables. A folder share
covers the notes currently in that folder (not nested subfolders); a direct
note share overrides a folder share when both apply.
"""

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import GUID


class NoteShare(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "note_shares"
    __table_args__ = (
        UniqueConstraint("note_id", "user_id", name="uq_note_share"),
    )

    note_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("notes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # viewer|editor


class FolderShare(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "folder_shares"
    __table_args__ = (
        UniqueConstraint("folder_id", "user_id", name="uq_folder_share"),
    )

    folder_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("folders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # viewer|editor

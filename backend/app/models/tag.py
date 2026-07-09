"""Tags — derived from #hashtags typed in note text, like iOS Notes.

The server extracts tags from body_text on every note save (see
services/tags.py), so clients never manage tag rows directly.
"""

import uuid

from sqlalchemy import Column, ForeignKey, String, Table, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import GUID

note_tags = Table(
    "note_tags",
    Base.metadata,
    Column(
        "note_id",
        GUID(),
        ForeignKey("notes.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        GUID(),
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Tag(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_tag_per_owner"),)

    owner_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Normalized lowercase, no leading '#'.
    name: Mapped[str] = mapped_column(String(100), nullable=False)

"""Attachments — uploaded files (images for now) embedded in notes.

The file lives on disk under MEDIA_ROOT/attachments with an unguessable
UUID name and is served at /media/attachments/<name> by nginx. The row
records ownership and metadata (and enables later garbage collection of
files no longer referenced by any note body).
"""

import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import GUID


class Attachment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "attachments"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # UUID-hex filename on disk (unguessable, doubles as the URL path).
    stored_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    original_name: Mapped[str] = mapped_column(String(400), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

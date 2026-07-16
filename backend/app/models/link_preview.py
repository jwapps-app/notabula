"""Cached link-unfurl metadata.

One row per URL, shared across all users and notes: the first person to
view a link fetches its title/description/image once, everyone else reads
the cache. Refreshed after a TTL so previews don't go stale forever.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class LinkPreview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "link_previews"

    # Uniqueness lives in a md5(url) expression index (see migration 0016) —
    # a plain unique btree on 2048 chars can exceed Postgres's index row limit.
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    site_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Whether the last fetch yielded anything usable (else we cache the miss
    # so a dead link isn't refetched on every render until the TTL lapses).
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

"""Garbage-collect orphaned attachments.

An uploaded image becomes an orphan when no note body references it —
e.g. the image was pasted then deleted, or its note was purged. We also
check revision bodies: as long as any history entry still references the
file, restoring that revision must bring the image back, so it stays.
(Revisions are capped per note, so truly abandoned files do age out.)

A second pass sweeps the media directory itself: deleting a user
cascades their attachment rows away, which strands the files where the
row-driven pass above can never find them. Any file no attachment row
claims gets removed.

A 1-day grace period (row age in the first pass, file mtime in the
second) avoids deleting a file that was uploaded moments ago and simply
hasn't been saved into a note body yet.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import Text, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Attachment, Note, NoteRevision

logger = logging.getLogger(__name__)

GRACE_PERIOD = timedelta(days=1)


async def purge_orphan_attachments(db: AsyncSession) -> int:
    cutoff = datetime.now(timezone.utc) - GRACE_PERIOD
    candidates = (
        (
            await db.execute(
                select(Attachment).where(Attachment.created_at < cutoff)
            )
        )
        .scalars()
        .all()
    )

    removed = 0
    for attachment in candidates:
        marker = f"%{attachment.stored_name}%"
        referenced_by_note = (
            await db.execute(
                select(Note.id).where(cast(Note.body, Text).like(marker)).limit(1)
            )
        ).scalar_one_or_none()
        if referenced_by_note is not None:
            continue
        referenced_by_revision = (
            await db.execute(
                select(NoteRevision.id)
                .where(cast(NoteRevision.body, Text).like(marker))
                .limit(1)
            )
        ).scalar_one_or_none()
        if referenced_by_revision is not None:
            continue

        path = Path(settings.media_root) / "attachments" / attachment.stored_name
        path.unlink(missing_ok=True)
        await db.delete(attachment)
        removed += 1

    removed += await _sweep_unclaimed_files(db, cutoff)
    return removed


async def _sweep_unclaimed_files(db: AsyncSession, cutoff: datetime) -> int:
    """Delete media files that no attachment row claims — e.g. left behind
    by a user deletion, whose rows cascaded away with the account."""
    media_dir = Path(settings.media_root) / "attachments"
    if not media_dir.is_dir():
        return 0

    known = set((await db.execute(select(Attachment.stored_name))).scalars().all())

    removed = 0
    for path in media_dir.iterdir():
        if not path.is_file() or path.name in known:
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue  # vanished mid-scan or unreadable — skip this run
        if mtime >= cutoff:
            continue
        path.unlink(missing_ok=True)
        removed += 1

    return removed

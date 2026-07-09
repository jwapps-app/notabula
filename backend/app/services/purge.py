"""Purge notes that have sat in Recently Deleted past the retention window.

Runs at startup and then daily (see main.py lifespan) — matching the iOS
Notes promise that Recently Deleted keeps notes for ~30 days.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Note

logger = logging.getLogger(__name__)

PURGE_INTERVAL_SECONDS = 24 * 60 * 60


async def purge_deleted_notes(db: AsyncSession) -> int:
    """Hard-delete notes whose deleted_at is older than the retention window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.purge_after_days)
    result = await db.execute(
        delete(Note).where(Note.deleted_at.is_not(None), Note.deleted_at < cutoff)
    )
    return result.rowcount or 0


async def purge_loop() -> None:
    """Background task: purge now, then once a day."""
    from app.services.attachment_gc import purge_orphan_attachments
    from app.services.scheduled_export import write_user_exports

    while True:
        try:
            async with AsyncSessionLocal() as db:
                count = await purge_deleted_notes(db)
                orphans = await purge_orphan_attachments(db)
                exported = await write_user_exports(db)
                await db.commit()
            if count:
                logger.info("Purged %d expired deleted note(s)", count)
            if orphans:
                logger.info("Removed %d orphaned attachment(s)", orphans)
            logger.info("Wrote export zips for %d user(s)", exported)
        except Exception:  # never let the loop die on a transient DB error
            logger.exception("Purge run failed; will retry next cycle")
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)

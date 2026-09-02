"""Purge notes that have sat in Recently Deleted past the retention window.

Runs at startup and then daily (see main.py lifespan) — matching the iOS
Notes promise that Recently Deleted keeps notes for ~30 days.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Note, Session

logger = logging.getLogger(__name__)

PURGE_INTERVAL_SECONDS = 24 * 60 * 60


async def purge_deleted_notes(db: AsyncSession) -> int:
    """Hard-delete notes whose deleted_at is older than the retention window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.purge_after_days)
    result = await db.execute(
        delete(Note).where(Note.deleted_at.is_not(None), Note.deleted_at < cutoff)
    )
    return result.rowcount or 0


async def purge_expired_sessions(db: AsyncSession) -> int:
    """Drop sessions past expires_at. They're already unusable (auth checks
    the expiry), but leaving them means every backup carries every stale
    token hash forever and the table only ever grows."""
    result = await db.execute(
        delete(Session).where(Session.expires_at < datetime.now(timezone.utc))
    )
    return result.rowcount or 0


async def _step(name: str, fn: Callable[[AsyncSession], Awaitable[int]]) -> int | None:
    """Run one maintenance job in its own session/transaction. A failure in
    one (say, a full disk during export) must not roll back the others."""
    try:
        async with AsyncSessionLocal() as db:
            result = await fn(db)
            await db.commit()
            return result
    except Exception:  # never let the loop die on a transient error
        logger.exception("%s failed; will retry next cycle", name)
        return None


async def purge_loop() -> None:
    """Background task: run every maintenance job now, then once a day."""
    from app.services.attachment_gc import purge_orphan_attachments
    from app.services.scheduled_export import write_user_exports

    while True:
        count = await _step("Note purge", purge_deleted_notes)
        sessions = await _step("Session sweep", purge_expired_sessions)
        orphans = await _step("Attachment GC", purge_orphan_attachments)
        exported = await _step("Export", write_user_exports)
        if count:
            logger.info("Purged %d expired deleted note(s)", count)
        if sessions:
            logger.info("Removed %d expired session(s)", sessions)
        if orphans:
            logger.info("Removed %d orphaned attachment(s)", orphans)
        if exported is not None:
            logger.info("Wrote export zips for %d user(s)", exported)
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)

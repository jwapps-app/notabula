"""Revision recording — collapses autosaves into editing sessions."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Note, NoteRevision

# Saves by the same editor within this window update the same revision.
COALESCE_MINUTES = 10
# Keep this many revisions per note; older ones are dropped.
MAX_REVISIONS_PER_NOTE = 100


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def record_revision(
    db: AsyncSession,
    note: Note,
    editor_id: uuid.UUID | None,
    guest_name: str | None = None,
) -> None:
    """Snapshot the note's current state as the latest revision.

    editor_id None = an anonymous secret-link visitor; guest_name is the
    name they gave. Two different guests never fold into one session even
    within the window — the identity is (editor_id, guest_name).
    """
    # version is the ordering key everywhere in history — it's strictly
    # increasing per note, unlike DB timestamps (SQLite stores CURRENT_
    # TIMESTAMP at second precision, which ties and mis-compares).
    latest = (
        await db.execute(
            select(NoteRevision)
            .where(NoteRevision.note_id == note.id)
            .order_by(NoteRevision.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=COALESCE_MINUTES)
    if (
        latest is not None
        and latest.editor_id == editor_id
        and latest.guest_name == guest_name
        and _aware(latest.updated_at) > cutoff
    ):
        # Same editing session — fold this save into it.
        latest.version = note.version
        latest.title = note.title
        latest.body = note.body
        latest.body_text = note.body_text
        return

    db.add(
        NoteRevision(
            note_id=note.id,
            editor_id=editor_id,
            guest_name=guest_name,
            version=note.version,
            title=note.title,
            body=note.body,
            body_text=note.body_text,
        )
    )
    await db.flush()

    # Trim history beyond the cap (oldest first).
    keep_ids = select(NoteRevision.id).where(
        NoteRevision.note_id == note.id
    ).order_by(NoteRevision.version.desc()).limit(MAX_REVISIONS_PER_NOTE)
    await db.execute(
        delete(NoteRevision).where(
            NoteRevision.note_id == note.id,
            ~NoteRevision.id.in_(keep_ids),
        )
    )

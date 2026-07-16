"""What deserves a push, and who gets it.

Events (all fan out through services.push, APNs + Web Push alike):
- share-created: the grantee learns a note/folder was shared with them.
- note-edited: every participant EXCEPT the editor learns a shared note
  changed. Fired once per editing session (record_revision's coalescing),
  never per autosave.
- guest-edited: the owner learns someone edited via the secret link.
- reminder: the owner's per-note remind_at came due (see reminder_loop).
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.models import FolderShare, Note, NoteShare
from app.services import push as push_service
from app.services.push import notify_user

logger = logging.getLogger(__name__)


def _title(note: Note) -> str:
    return note.title or "Untitled note"


async def participants(db: AsyncSession, note: Note) -> set[uuid.UUID]:
    """Everyone with access: the owner, direct sharees, folder sharees."""
    direct = (
        (
            await db.execute(
                select(NoteShare.user_id).where(NoteShare.note_id == note.id)
            )
        )
        .scalars()
        .all()
    )
    via_folder = (
        (
            await db.execute(
                select(FolderShare.user_id).where(
                    FolderShare.folder_id == note.folder_id
                )
            )
        )
        .scalars()
        .all()
    )
    return {note.owner_id, *direct, *via_folder}


async def notify_share_created(
    db: AsyncSession, *, grantee_id: uuid.UUID, granter_name: str, what: str
) -> None:
    await notify_user(
        db,
        grantee_id,
        title="Shared with you",
        body=f"{granter_name} shared “{what}”",
        data={"type": "share"},
    )


async def notify_note_edited(
    db: AsyncSession, note: Note, *, editor_id: uuid.UUID, editor_name: str
) -> None:
    """Tell every other participant. No-op for unshared notes."""
    people = await participants(db, note)
    people.discard(editor_id)
    if not people:
        return
    # Resolve every participant's targets in two queries (was 2 per person).
    # Late-bound via the module so tests can monkeypatch push.deliver.
    targets_by_user = await push_service.targets_for_users(db, people)
    for targets in targets_by_user.values():
        push_service.deliver(
            targets,
            title=_title(note),
            body=f"{editor_name} made changes",
            data={"type": "edit", "note_id": str(note.id)},
        )


async def notify_guest_edited(
    db: AsyncSession, note: Note, *, guest_name: str | None
) -> None:
    who = guest_name or "Someone"
    await notify_user(
        db,
        note.owner_id,
        title=_title(note),
        body=f"{who} edited via your shared link",
        data={"type": "guest-edit", "note_id": str(note.id)},
    )


# --- Reminders -----------------------------------------------------------

REMINDER_POLL_SECONDS = 60


async def fire_due_reminders(db: AsyncSession) -> int:
    """Push every reminder that has come due; returns how many fired."""
    now = datetime.now(timezone.utc)
    due = (
        (
            await db.execute(
                select(Note)
                # Only title/owner_id/reminded_at are touched — skip the bodies.
                .options(defer(Note.body), defer(Note.cipher_body), defer(Note.body_text))
                .where(
                    Note.remind_at.is_not(None),
                    Note.remind_at <= now,
                    Note.reminded_at.is_(None),
                    Note.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for note in due:
        await notify_user(
            db,
            note.owner_id,
            title="Reminder",
            body=_title(note),
            data={"type": "reminder", "note_id": str(note.id)},
        )
        note.reminded_at = now
    return len(due)


async def reminder_loop() -> None:
    """Background task: check for due reminders once a minute."""
    from app.database import AsyncSessionLocal

    while True:
        try:
            async with AsyncSessionLocal() as db:
                fired = await fire_due_reminders(db)
                await db.commit()
            if fired:
                logger.info("Fired %d reminder(s)", fired)
        except Exception:  # keep the loop alive through transient DB errors
            logger.exception("Reminder sweep failed; retrying next cycle")
        await asyncio.sleep(REMINDER_POLL_SECONDS)

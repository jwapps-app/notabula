"""Public note access — no authentication, gated only by the link token.

Guests with an "editor" link save through the same optimistic-versioning
path as logged-in users, and their edits are recorded in the note's
history (attributed to "guest"), so the redline + restore make anonymous
editing safe: nothing is ever truly lost.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import settings
from app.core.deps import DB
from app.models import Note, NoteLink
from app.services.notifications import notify_guest_edited
from app.services.revisions import record_revision
from app.services.tags import sync_note_tags

router = APIRouter(prefix="/public", tags=["public"])


class PublicNote(BaseModel):
    title: str
    body: dict | None
    body_text: str
    updated_at: datetime
    version: int
    role: str
    app_name: str


class PublicNoteUpdate(BaseModel):
    base_version: int
    body: dict | None = None
    body_text: str | None = None
    title: str | None = Field(default=None, max_length=400)
    # The name the guest gave themselves, shown in the note's history.
    guest_name: str | None = Field(default=None, max_length=80)


async def _linked_note(db, token: str, *, for_update: bool = False) -> tuple[Note, NoteLink]:
    link = (
        await db.execute(select(NoteLink).where(NoteLink.token == token))
    ).scalar_one_or_none()
    # for_update: row-lock on mutation paths so the base_version check can't
    # race a concurrent writer (lost update).
    note = None if link is None else await db.get(Note, link.note_id, with_for_update=for_update)
    if link is None or note is None or note.deleted_at is not None or note.locked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Link not found"
        )
    return note, link


def _public(note: Note, role: str) -> PublicNote:
    return PublicNote(
        title=note.title,
        body=note.body,
        body_text=note.body_text,
        updated_at=note.updated_at,
        version=note.version,
        role=role,
        app_name=settings.app_name,
    )


@router.get("/notes/{token}", response_model=PublicNote)
async def get_public_note(token: str, db: DB) -> PublicNote:
    note, link = await _linked_note(db, token)
    return _public(note, link.role)


@router.patch("/notes/{token}", response_model=PublicNote)
async def update_public_note(
    token: str, payload: PublicNoteUpdate, db: DB
) -> PublicNote:
    note, link = await _linked_note(db, token, for_update=True)
    if link.role != "editor":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This link is view-only",
        )
    # A name is mandatory for guest edits — it's the only attribution the
    # note's history gets. (They can type anything, but it marks a person.)
    guest = (payload.guest_name or "").strip()
    if not guest:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter your name before editing",
        )
    if payload.base_version != note.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Note was modified elsewhere; reload before saving",
        )

    if payload.body is not None:
        note.body = payload.body
    if payload.body_text is not None:
        note.body_text = payload.body_text
        await sync_note_tags(db, note, note.owner_id)
    if payload.title is not None:
        note.title = payload.title

    note.version += 1
    await db.flush()
    # editor_id=None + the guest's name → "Sue (guest)" in the history.
    new_session = await record_revision(db, note, None, guest_name=guest)
    if new_session:
        await notify_guest_edited(db, note, guest_name=guest)
    await db.refresh(note)
    return _public(note, link.role)

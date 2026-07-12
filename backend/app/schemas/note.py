"""Note request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# A generous ceiling on note content: enough for very long notes, but a
# bound so a single request can't bloat storage / the search vector or
# stall the per-save hashtag regex with a multi-megabyte body.
MAX_BODY_TEXT = 1_000_000


class NoteCreate(BaseModel):
    folder_id: uuid.UUID
    body: dict | None = None
    body_text: str = Field(default="", max_length=MAX_BODY_TEXT)
    title: str = Field(default="", max_length=400)


class NoteUpdate(BaseModel):
    """Partial update. `base_version` is the version this edit was based on —
    the server rejects the write (409) if the note has moved past it."""

    base_version: int
    folder_id: uuid.UUID | None = None
    body: dict | None = None
    body_text: str | None = Field(default=None, max_length=MAX_BODY_TEXT)
    title: str | None = Field(default=None, max_length=400)
    pinned: bool | None = None
    # Locked notes (owner only): locked=True with cipher_body encrypts;
    # locked=False with plaintext body decrypts; cipher_body alone re-saves
    # an already-locked note's content.
    locked: bool | None = None
    cipher_body: str | None = None
    # Reminder: a datetime sets it, explicit null clears it. Distinguished
    # from "not sent" via model_fields_set in the handler.
    remind_at: datetime | None = None


class NoteListItem(BaseModel):
    """Lightweight shape for the note list pane — no body JSON."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    folder_id: uuid.UUID
    title: str
    preview: str = ""
    # Gallery-view thumbnail: first image URL in the body, if any.
    thumb: str | None = None
    pinned: bool
    locked: bool = False
    remind_at: datetime | None = None
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    # My relationship to the note; owner unless it reached me via a share.
    role: str = "owner"
    owner_name: str | None = None


class RevisionListItem(BaseModel):
    """One editing session in a note's history."""

    id: uuid.UUID
    version: int
    editor_name: str
    created_at: datetime
    updated_at: datetime


class RevisionDetail(RevisionListItem):
    title: str
    body: dict | None
    body_text: str
    # The state before this session — what the redline diffs against.
    prev_body_text: str


class NoteOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    folder_id: uuid.UUID
    title: str
    body: dict | None
    body_text: str
    pinned: bool
    locked: bool = False
    cipher_body: str | None = None
    remind_at: datetime | None = None
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    role: str = "owner"
    owner_name: str | None = None  # set when the note reached me via a share

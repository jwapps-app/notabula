"""Note request/response schemas."""

import json
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# A generous ceiling on note content: enough for very long notes, but a
# bound so a single request can't bloat storage / the search vector or
# stall the per-save hashtag regex with a multi-megabyte body.
MAX_BODY_TEXT = 1_000_000
# The ProseMirror JSON is what's actually stored (and snapshotted into up
# to 100 revisions per note), so it gets its own ceiling. Twice the text
# limit leaves room for markup around a maximal body_text.
MAX_BODY_JSON = 2 * MAX_BODY_TEXT
# Ciphertext of a locked note: base64 of the encrypted JSON, so ~4/3 of the
# body ceiling plus the blob envelope.
MAX_CIPHER_BODY = 4_000_000


def validate_body(v: dict | None) -> dict | None:
    """Shape and size check for a stored document. Bodies arrive from every
    client and from imports, so 'a dict' is not enough: it must be a
    ProseMirror doc, and it must fit."""
    if v is None:
        return v
    if v.get("type") != "doc":
        raise ValueError("body must be a ProseMirror document (type: doc)")
    if len(json.dumps(v, separators=(",", ":"))) > MAX_BODY_JSON:
        raise ValueError(f"body exceeds {MAX_BODY_JSON // 1_000_000} MB")
    return v


class NoteCreate(BaseModel):
    folder_id: uuid.UUID
    body: dict | None = None
    body_text: str = Field(default="", max_length=MAX_BODY_TEXT)
    title: str = Field(default="", max_length=400)

    _body = field_validator("body")(validate_body)


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
    cipher_body: str | None = Field(default=None, max_length=MAX_CIPHER_BODY)

    _body = field_validator("body")(validate_body)
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

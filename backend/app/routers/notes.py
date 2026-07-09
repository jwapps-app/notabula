"""Note CRUD with soft delete and optimistic concurrency.

List responses are lightweight (no body JSON) — the editor fetches the
full note on open. Sorting matches iOS Notes: pinned first, then most
recently edited.
"""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import Text, and_, cast, or_, select
from sqlalchemy.orm import defer

from app.core.deps import DB, CurrentUser
from app.core.security import hash_token
from app.models import (
    Folder,
    FolderShare,
    Note,
    NoteLink,
    NoteRevision,
    NoteShare,
    Tag,
    User,
)
from app.models import Session as SessionModel
from app.schemas.note import (
    NoteCreate,
    NoteListItem,
    NoteOut,
    NoteUpdate,
    RevisionDetail,
    RevisionListItem,
)
from app.services.access import note_role, resolved_role, share_maps
from app.services.revisions import record_revision
from app.services.tags import sweep_orphan_tags, sync_note_tags

router = APIRouter(prefix="/notes", tags=["notes"])

PREVIEW_LEN = 120


def _preview(note: Note) -> str:
    """Second-line preview, like the iOS Notes list."""
    if note.locked:
        return "Locked"
    text = note.body_text or ""
    if note.title and text.startswith(note.title):
        text = text[len(note.title) :]
    return text.strip().replace("\n", " ")[:PREVIEW_LEN]


def _list_item(note: Note, role: str = "owner", owner_name: str | None = None) -> NoteListItem:
    item = NoteListItem.model_validate(note)
    item.preview = _preview(note)
    item.role = role
    item.owner_name = owner_name
    return item


async def _accessible_note(db, user, note_id: uuid.UUID) -> tuple[Note, str]:
    """Return (note, role) or 404. Callers enforce the role they need."""
    note = await db.get(Note, note_id)
    role = None if note is None else await note_role(db, user, note)
    if note is None or role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return note, role


def _note_out(note: Note, role: str) -> NoteOut:
    out = NoteOut.model_validate(note)
    out.role = role
    return out


async def _require_unshared(db, note: Note) -> None:
    """Locking demands exclusivity: no user shares, no public links."""
    shared = (
        await db.execute(select(NoteShare.id).where(NoteShare.note_id == note.id).limit(1))
    ).scalar_one_or_none()
    linked = (
        await db.execute(select(NoteLink.id).where(NoteLink.note_id == note.id).limit(1))
    ).scalar_one_or_none()
    if shared is not None or linked is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Remove shares and public links before locking this note",
        )


def _editor_name(rev: NoteRevision, account_name: str | None) -> str:
    """How a revision's author is shown in history."""
    if account_name:
        return account_name
    if rev.guest_name:
        return f"{rev.guest_name} (guest)"
    return "guest"


@router.get("", response_model=list[NoteListItem])
async def list_notes(
    user: CurrentUser,
    db: DB,
    folder_id: uuid.UUID | None = Query(default=None),
    tag: str | None = Query(default=None, description="Filter by tag name (all folders)"),
    deleted: bool = Query(default=False, description="Recently Deleted view"),
    shared: bool = Query(default=False, description="Notes shared with me"),
    view: str | None = Query(
        default=None, description="Smart view: media|links|tasks|locked|recent"
    ),
) -> list[NoteListItem]:
    if shared:
        return await _list_shared_notes(db, user)

    if view is not None:
        return await _list_smart_view(db, user, view)

    if folder_id is not None:
        folder = await db.get(Folder, folder_id)
        if folder is not None and folder.owner_id != user.id:
            return await _list_shared_folder_notes(db, user, folder)

    query = (
        select(Note)
        .options(defer(Note.body), defer(Note.cipher_body))
        .where(Note.owner_id == user.id)
    )
    if deleted:
        query = query.where(Note.deleted_at.is_not(None))
    elif tag is not None:
        # A tag view spans folders, like iOS Notes.
        query = query.where(
            Note.deleted_at.is_(None),
            Note.tags.any(Tag.name == tag.lower()),
        )
    else:
        query = query.where(Note.deleted_at.is_(None))
        if folder_id is not None:
            query = query.where(Note.folder_id == folder_id)
    query = query.order_by(Note.pinned.desc(), Note.updated_at.desc())
    result = await db.execute(query)
    return [_list_item(n) for n in result.scalars()]


async def _list_smart_view(db, user, view: str) -> list[NoteListItem]:
    """Automatic collections computed from note content — no filing needed.
    Owner-scoped: smart views search my notes, like search does."""
    body_text_col = cast(Note.body, Text)
    query = (
        select(Note)
        .options(defer(Note.body), defer(Note.cipher_body))
        .where(Note.owner_id == user.id, Note.deleted_at.is_(None))
    )
    if view == "media":
        # ProseMirror image nodes; both JSON spacings for dialect safety.
        query = query.where(
            or_(
                body_text_col.like('%"type": "image"%'),
                body_text_col.like('%"type":"image"%'),
            )
        )
    elif view == "links":
        query = query.where(
            or_(Note.body_text.like("%http://%"), Note.body_text.like("%https://%"))
        )
    elif view == "tasks":
        # Notes with UNCHECKED to-dos — the cross-note "still undone" list.
        query = query.where(
            or_(
                body_text_col.like('%"checked": false%'),
                body_text_col.like('%"checked":false%'),
            )
        )
    elif view == "locked":
        query = query.where(Note.locked.is_(True))
    elif view == "recent":
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        query = query.where(Note.updated_at > cutoff)
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unknown view",
        )
    result = await db.execute(query.order_by(Note.pinned.desc(), Note.updated_at.desc()))
    return [_list_item(n) for n in result.scalars()]


async def _list_shared_notes(db, user) -> list[NoteListItem]:
    """Everything shared with me — direct note shares + shared folders.
    Locked notes never appear: they're owner-only while locked."""
    note_roles, folder_roles = await share_maps(db, user)
    if not note_roles and not folder_roles:
        return []
    result = await db.execute(
        select(Note, User.name)
        .options(defer(Note.body), defer(Note.cipher_body))
        .join(User, User.id == Note.owner_id)
        .where(
            Note.deleted_at.is_(None),
            Note.locked.is_(False),
            or_(
                Note.id.in_(note_roles.keys()) if note_roles else False,
                Note.folder_id.in_(folder_roles.keys()) if folder_roles else False,
            ),
        )
        .order_by(Note.pinned.desc(), Note.updated_at.desc())
    )
    items = []
    for note, owner_name in result.all():
        role = resolved_role(note, user.id, note_roles, folder_roles)
        items.append(_list_item(note, role=role, owner_name=owner_name))
    return items


async def _list_shared_folder_notes(db, user, folder: Folder) -> list[NoteListItem]:
    """Contents of someone else's folder that was shared with me."""
    share = (
        await db.execute(
            select(FolderShare.role).where(
                FolderShare.folder_id == folder.id, FolderShare.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if share is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
    owner = await db.get(User, folder.owner_id)
    result = await db.execute(
        select(Note)
        .options(defer(Note.body), defer(Note.cipher_body))
        .where(
            Note.folder_id == folder.id,
            Note.deleted_at.is_(None),
            Note.locked.is_(False),  # locked notes stay owner-only
        )
        .order_by(Note.pinned.desc(), Note.updated_at.desc())
    )
    return [
        _list_item(n, role=share, owner_name=owner.name if owner else None)
        for n in result.scalars()
    ]


from pydantic import BaseModel, Field  # noqa: E402  (import endpoint schemas)


class ImportNoteIn(BaseModel):
    folder: str = Field(default="Notes", max_length=200)
    title: str = Field(default="", max_length=400)
    body: dict | None = None
    body_text: str = ""
    pinned: bool = False
    locked: bool = False
    cipher_body: str | None = None
    created_at: datetime
    updated_at: datetime


class NotesImportRequest(BaseModel):
    notes: list[ImportNoteIn] = Field(max_length=1000)


@router.post("/import", status_code=201)
async def import_notes(payload: NotesImportRequest, user: CurrentUser, db: DB) -> dict:
    """Round-trip import — consumes this app's own export format.

    Folders are created on demand by name; original timestamps are kept;
    locked notes pass their ciphertext straight through (they unlock with
    whatever password they were exported under). Importing the same file
    twice creates duplicates — this is a restore tool, not a sync.
    """
    folder_cache: dict[str, Folder] = {
        f.name: f
        for f in (await db.execute(select(Folder).where(Folder.owner_id == user.id))).scalars()
    }

    async def folder_for(name: str) -> Folder:
        name = name.strip() or "Notes"
        if name not in folder_cache:
            folder = Folder(owner_id=user.id, name=name)
            db.add(folder)
            await db.flush()
            folder_cache[name] = folder
        return folder_cache[name]

    for item in payload.notes:
        folder = await folder_for(item.folder)
        note = Note(
            owner_id=user.id,
            folder_id=folder.id,
            title=item.title,
            body=None if item.locked else item.body,
            body_text="" if item.locked else item.body_text,
            pinned=item.pinned,
            locked=item.locked,
            cipher_body=item.cipher_body if item.locked else None,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        db.add(note)
        await db.flush()
        if not note.locked:
            await sync_note_tags(db, note, user.id, sweep_orphans=False)
            await record_revision(db, note, user.id)

    await sweep_orphan_tags(db, user.id)
    return {"imported": len(payload.notes)}


@router.post("/capture", response_model=NoteOut, status_code=201)
async def capture_note(
    request: Request,
    db: DB,
    token: str | None = Query(default=None),
) -> NoteOut:
    """One-shot capture for external tools — chiefly the iOS Shortcut that
    puts "Share to Notabula" in the share sheet (iOS PWAs can't register
    as share targets). Plain text in, note in the default folder out;
    the first line becomes the title, #hashtags become tags.

    Auth is deliberately flexible for Shortcuts: a Bearer header OR a
    ?token= query param (the Settings page hands out a personalized
    capture link so the Shortcut needs zero header configuration). The
    body may be JSON {"text": ...} or plain text."""
    bearer = request.headers.get("authorization", "")
    raw_token = token or (bearer[7:] if bearer.lower().startswith("bearer ") else "")
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    session = (
        await db.execute(
            select(SessionModel).where(SessionModel.token_hash == hash_token(raw_token))
        )
    ).scalar_one_or_none()
    if session is None or session.expires_at.replace(tzinfo=timezone.utc) < datetime.now(
        timezone.utc
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    user = await db.get(User, session.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    body_bytes = await request.body()
    text = ""
    if (request.headers.get("content-type") or "").startswith("application/json"):
        try:
            parsed = json.loads(body_bytes)
            text = str(parsed.get("text", "")) if isinstance(parsed, dict) else ""
        except ValueError:
            text = ""
    else:
        text = body_bytes.decode("utf-8", errors="replace")
    text = text.strip()[:100_000]
    if not text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nothing to capture — share some text or a link",
        )
    # A bare shared link is unreadable as a note title — unfurl it so the
    # first line is the page title, with the URL beneath (still a link).
    if re.fullmatch(r"https?://\S+", text):
        from app.services.unfurl import fetch_preview

        preview = await fetch_preview(text)
        if preview and preview.get("title"):
            text = f"{preview['title']}\n{text}"
    lines = text.split("\n")
    body = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                **({"content": [{"type": "text", "text": line}]} if line.strip() else {}),
            }
            for line in lines
        ],
    }
    title = next((ln.strip() for ln in lines if ln.strip()), "")[:400]

    folder = (
        (await db.execute(select(Folder).where(Folder.owner_id == user.id, Folder.is_default)))
        .scalars()
        .first()
    )
    if folder is None:  # extremely defensive: every account gets one at signup
        folder = Folder(owner_id=user.id, name="Notes", is_default=True)
        db.add(folder)
        await db.flush()

    note = Note(
        owner_id=user.id,
        folder_id=folder.id,
        title=title,
        body=body,
        body_text=text,
    )
    db.add(note)
    await db.flush()
    await sync_note_tags(db, note, user.id)
    await record_revision(db, note, user.id)
    return NoteOut.model_validate(note)


@router.post("", response_model=NoteOut, status_code=201)
async def create_note(payload: NoteCreate, user: CurrentUser, db: DB) -> NoteOut:
    folder = await db.get(Folder, payload.folder_id)
    if folder is None or folder.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
    note = Note(
        owner_id=user.id,
        folder_id=payload.folder_id,
        title=payload.title,
        body=payload.body,
        body_text=payload.body_text,
    )
    db.add(note)
    await db.flush()
    await sync_note_tags(db, note, user.id)
    await record_revision(db, note, user.id)
    return NoteOut.model_validate(note)


@router.get("/sync", response_model=list[NoteOut])
async def sync_notes(user: CurrentUser, db: DB) -> list[NoteOut]:
    """Every accessible note IN FULL (bodies included), one request.

    This hydrates the PWA's offline cache: own notes plus everything
    shared with me, so the app can read and edit with the server gone.
    (Registered before /{note_id} so "sync" isn't parsed as a UUID.)
    """
    note_roles, folder_roles = await share_maps(db, user)
    accessible = or_(
        Note.owner_id == user.id,  # own locked notes sync (as ciphertext)
        and_(
            Note.locked.is_(False),  # …but nobody else's locked notes do
            or_(
                Note.id.in_(note_roles.keys()) if note_roles else False,
                Note.folder_id.in_(folder_roles.keys()) if folder_roles else False,
            ),
        ),
    )
    result = await db.execute(
        select(Note, User.name)
        .join(User, User.id == Note.owner_id)
        .where(accessible, Note.deleted_at.is_(None))
        .order_by(Note.updated_at.desc())
    )
    out = []
    for note, owner_name in result.all():
        role = resolved_role(note, user.id, note_roles, folder_roles)
        item = _note_out(note, role)
        if note.owner_id != user.id:
            item.owner_name = owner_name
        out.append(item)
    return out


@router.get("/{note_id}", response_model=NoteOut)
async def get_note(note_id: uuid.UUID, user: CurrentUser, db: DB) -> NoteOut:
    note, role = await _accessible_note(db, user, note_id)
    return _note_out(note, role)


@router.patch("/{note_id}", response_model=NoteOut)
async def update_note(
    note_id: uuid.UUID, payload: NoteUpdate, user: CurrentUser, db: DB
) -> NoteOut:
    note, role = await _accessible_note(db, user, note_id)
    if role == "viewer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You have view-only access to this note",
        )

    # Optimistic concurrency: refuse to clobber an edit made elsewhere.
    if payload.base_version != note.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Note was modified elsewhere; reload before saving",
        )

    if payload.folder_id is not None:
        # Only the owner files a note — editors edit content, not location.
        if role != "owner":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the owner can move this note",
            )
        folder = await db.get(Folder, payload.folder_id)
        if folder is None or folder.owner_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
        note.folder_id = payload.folder_id

    # --- Lock state machine (owner only) --------------------------------
    lock_change = payload.locked is not None and payload.locked != note.locked
    if (lock_change or payload.cipher_body is not None) and role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can lock or unlock this note",
        )
    if lock_change and payload.locked:
        # Locking: refuse while shared/linked, then swap plaintext for cipher.
        await _require_unshared(db, note)
        if not payload.cipher_body:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Locking requires the encrypted body",
            )
        note.locked = True
        note.cipher_body = payload.cipher_body
        note.body = None
        note.body_text = ""
        await sync_note_tags(db, note, note.owner_id)  # clears tags
        if payload.title is not None:
            note.title = payload.title
    elif lock_change and not payload.locked:
        # Unlocking: the client sends the decrypted content back.
        if payload.body is None or payload.body_text is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Unlocking requires the decrypted content",
            )
        note.locked = False
        note.cipher_body = None
        note.body = payload.body
        note.body_text = payload.body_text
        await sync_note_tags(db, note, note.owner_id)
        if payload.title is not None:
            note.title = payload.title
    elif note.locked:
        # Editing while locked: only new ciphertext, title, or pin.
        if payload.body is not None or payload.body_text is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This note is locked — unlock it to edit",
            )
        if payload.cipher_body is not None:
            note.cipher_body = payload.cipher_body
        if payload.title is not None:
            note.title = payload.title
        if payload.pinned is not None:
            note.pinned = payload.pinned
    else:
        if payload.cipher_body is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ciphertext is only accepted for locked notes",
            )
        if payload.body is not None:
            note.body = payload.body
        if payload.body_text is not None:
            note.body_text = payload.body_text
            # Tags always belong to the note's owner, whoever edited.
            await sync_note_tags(db, note, note.owner_id)
        if payload.title is not None:
            note.title = payload.title
        if payload.pinned is not None:
            note.pinned = payload.pinned

    note.version += 1
    await db.flush()
    # Content changes go into history — but never while the note is locked
    # (recording ciphertext or empty bodies would be noise, and plaintext
    # history would defeat the lock). Unlocking resumes history.
    if not note.locked and any(
        v is not None for v in (payload.body, payload.body_text, payload.title)
    ):
        await record_revision(db, note, user.id)
    # updated_at is computed server-side (onupdate=now()); refresh so the
    # response carries the real value instead of an expired attribute.
    await db.refresh(note)
    return _note_out(note, role)


@router.delete("/{note_id}", status_code=204)
async def delete_note(
    note_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    permanent: bool = Query(default=False),
) -> None:
    """Soft delete → Recently Deleted; `permanent=true` removes for good.
    Owner only — shared users never delete someone else's note."""
    note, role = await _accessible_note(db, user, note_id)
    if role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can delete this note",
        )
    if permanent or note.deleted_at is not None:
        await db.delete(note)
    else:
        note.deleted_at = datetime.now(timezone.utc)


@router.get("/{note_id}/revisions", response_model=list[RevisionListItem])
async def list_revisions(note_id: uuid.UUID, user: CurrentUser, db: DB) -> list[RevisionListItem]:
    """Edit history, newest first. Anyone with access may read it."""
    await _accessible_note(db, user, note_id)
    result = await db.execute(
        select(NoteRevision, User.name)
        .join(User, User.id == NoteRevision.editor_id, isouter=True)
        .where(NoteRevision.note_id == note_id)
        .order_by(NoteRevision.version.desc())
    )
    return [
        RevisionListItem(
            id=rev.id,
            version=rev.version,
            editor_name=_editor_name(rev, name),
            created_at=rev.created_at,
            updated_at=rev.updated_at,
        )
        for rev, name in result.all()
    ]


@router.get("/{note_id}/revisions/{revision_id}", response_model=RevisionDetail)
async def get_revision(
    note_id: uuid.UUID, revision_id: uuid.UUID, user: CurrentUser, db: DB
) -> RevisionDetail:
    await _accessible_note(db, user, note_id)
    row = (
        await db.execute(
            select(NoteRevision, User.name)
            .join(User, User.id == NoteRevision.editor_id, isouter=True)
            .where(NoteRevision.id == revision_id, NoteRevision.note_id == note_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Revision not found")
    rev, name = row
    prev = (
        await db.execute(
            select(NoteRevision.body_text)
            .where(
                NoteRevision.note_id == note_id,
                NoteRevision.version < rev.version,
            )
            .order_by(NoteRevision.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return RevisionDetail(
        id=rev.id,
        version=rev.version,
        editor_name=_editor_name(rev, name),
        created_at=rev.created_at,
        updated_at=rev.updated_at,
        title=rev.title,
        body=rev.body,
        body_text=rev.body_text,
        prev_body_text=prev or "",
    )


@router.post("/{note_id}/restore", response_model=NoteOut)
async def restore_note(note_id: uuid.UUID, user: CurrentUser, db: DB) -> NoteOut:
    note = await db.get(Note, note_id)
    if note is None or note.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    note.deleted_at = None
    await db.flush()
    await db.refresh(note)
    return _note_out(note, "owner")

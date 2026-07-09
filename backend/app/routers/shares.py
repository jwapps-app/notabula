"""Share management — owners grant/revoke access by username.

Shares target existing usernames (there is no email in this system).
Only the owner of a note/folder manages its shares.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import DB, CurrentUser
from app.core.security import generate_token
from app.models import Folder, FolderShare, Note, NoteLink, NoteShare, User

router = APIRouter(tags=["shares"])


class ShareRequest(BaseModel):
    username: str
    role: Literal["viewer", "editor"]


class ShareOut(BaseModel):
    username: str
    name: str
    role: str


class SharedFolderOut(BaseModel):
    id: uuid.UUID
    name: str
    owner_name: str
    role: str


async def _owned_note(db, user, note_id: uuid.UUID) -> Note:
    note = await db.get(Note, note_id)
    if note is None or note.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return note


async def _owned_folder(db, user, folder_id: uuid.UUID) -> Folder:
    folder = await db.get(Folder, folder_id)
    if folder is None or folder.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
    return folder


async def _grantee(db, user, username: str) -> User:
    grantee = (
        await db.execute(select(User).where(User.username == username.strip().lower()))
    ).scalar_one_or_none()
    if grantee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such user")
    if grantee.id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You already own this",
        )
    return grantee


async def _list_shares(db, model, target_col, target_id) -> list[ShareOut]:
    result = await db.execute(
        select(model.role, User.username, User.name)
        .join(User, User.id == model.user_id)
        .where(target_col == target_id)
        .order_by(User.username)
    )
    return [ShareOut(role=r, username=u, name=n) for r, u, n in result.all()]


# --- Note shares ---------------------------------------------------------


@router.get("/notes/{note_id}/shares", response_model=list[ShareOut])
async def list_note_shares(note_id: uuid.UUID, user: CurrentUser, db: DB):
    await _owned_note(db, user, note_id)
    return await _list_shares(db, NoteShare, NoteShare.note_id, note_id)


@router.put("/notes/{note_id}/shares", response_model=list[ShareOut])
async def share_note(
    note_id: uuid.UUID, payload: ShareRequest, user: CurrentUser, db: DB
):
    note = await _owned_note(db, user, note_id)
    if note.locked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Locked notes cannot be shared — unlock it first",
        )
    grantee = await _grantee(db, user, payload.username)
    existing = (
        await db.execute(
            select(NoteShare).where(
                NoteShare.note_id == note.id, NoteShare.user_id == grantee.id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(NoteShare(note_id=note.id, user_id=grantee.id, role=payload.role))
    else:
        existing.role = payload.role
    await db.flush()
    return await _list_shares(db, NoteShare, NoteShare.note_id, note_id)


@router.delete("/notes/{note_id}/shares/{username}", response_model=list[ShareOut])
async def unshare_note(
    note_id: uuid.UUID, username: str, user: CurrentUser, db: DB
):
    note = await _owned_note(db, user, note_id)
    grantee = await _grantee(db, user, username)
    share = (
        await db.execute(
            select(NoteShare).where(
                NoteShare.note_id == note.id, NoteShare.user_id == grantee.id
            )
        )
    ).scalar_one_or_none()
    if share is not None:
        await db.delete(share)
        await db.flush()
    return await _list_shares(db, NoteShare, NoteShare.note_id, note_id)


# --- Folder shares -------------------------------------------------------


@router.get("/folders/{folder_id}/shares", response_model=list[ShareOut])
async def list_folder_shares(folder_id: uuid.UUID, user: CurrentUser, db: DB):
    await _owned_folder(db, user, folder_id)
    return await _list_shares(db, FolderShare, FolderShare.folder_id, folder_id)


@router.put("/folders/{folder_id}/shares", response_model=list[ShareOut])
async def share_folder(
    folder_id: uuid.UUID, payload: ShareRequest, user: CurrentUser, db: DB
):
    folder = await _owned_folder(db, user, folder_id)
    grantee = await _grantee(db, user, payload.username)
    existing = (
        await db.execute(
            select(FolderShare).where(
                FolderShare.folder_id == folder.id, FolderShare.user_id == grantee.id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(FolderShare(folder_id=folder.id, user_id=grantee.id, role=payload.role))
    else:
        existing.role = payload.role
    await db.flush()
    return await _list_shares(db, FolderShare, FolderShare.folder_id, folder_id)


@router.delete("/folders/{folder_id}/shares/{username}", response_model=list[ShareOut])
async def unshare_folder(
    folder_id: uuid.UUID, username: str, user: CurrentUser, db: DB
):
    folder = await _owned_folder(db, user, folder_id)
    grantee = await _grantee(db, user, username)
    share = (
        await db.execute(
            select(FolderShare).where(
                FolderShare.folder_id == folder.id, FolderShare.user_id == grantee.id
            )
        )
    ).scalar_one_or_none()
    if share is not None:
        await db.delete(share)
        await db.flush()
    return await _list_shares(db, FolderShare, FolderShare.folder_id, folder_id)


# --- Public links ("anyone with the link") --------------------------------


class LinkRequest(BaseModel):
    role: Literal["viewer", "editor"]


class LinkOut(BaseModel):
    token: str
    role: str


@router.get("/notes/{note_id}/link", response_model=LinkOut | None)
async def get_note_link(note_id: uuid.UUID, user: CurrentUser, db: DB):
    note = await _owned_note(db, user, note_id)
    link = (
        await db.execute(select(NoteLink).where(NoteLink.note_id == note.id))
    ).scalar_one_or_none()
    return None if link is None else LinkOut(token=link.token, role=link.role)


@router.put("/notes/{note_id}/link", response_model=LinkOut)
async def upsert_note_link(
    note_id: uuid.UUID, payload: LinkRequest, user: CurrentUser, db: DB
):
    """Create the note's public link (or change its role). One per note."""
    note = await _owned_note(db, user, note_id)
    if note.locked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Locked notes cannot have a public link — unlock it first",
        )
    link = (
        await db.execute(select(NoteLink).where(NoteLink.note_id == note.id))
    ).scalar_one_or_none()
    if link is None:
        link = NoteLink(note_id=note.id, token=generate_token(16), role=payload.role)
        db.add(link)
        await db.flush()
    else:
        link.role = payload.role
    return LinkOut(token=link.token, role=link.role)


@router.delete("/notes/{note_id}/link", status_code=204)
async def revoke_note_link(note_id: uuid.UUID, user: CurrentUser, db: DB) -> None:
    note = await _owned_note(db, user, note_id)
    link = (
        await db.execute(select(NoteLink).where(NoteLink.note_id == note.id))
    ).scalar_one_or_none()
    if link is not None:
        await db.delete(link)


# --- Shared with me ------------------------------------------------------


@router.get("/shared/folders", response_model=list[SharedFolderOut])
async def shared_folders(user: CurrentUser, db: DB):
    """Folders other people shared with me (for the sidebar)."""
    result = await db.execute(
        select(Folder.id, Folder.name, User.name, FolderShare.role)
        .join(FolderShare, FolderShare.folder_id == Folder.id)
        .join(User, User.id == Folder.owner_id)
        .where(FolderShare.user_id == user.id)
        .order_by(Folder.name)
    )
    return [
        SharedFolderOut(id=fid, name=fname, owner_name=oname, role=role)
        for fid, fname, oname, role in result.all()
    ]

"""Folder CRUD — always scoped to the authenticated owner."""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.core.deps import DB, CurrentUser
from app.models import Folder, Note
from app.schemas.folder import FolderCreate, FolderOut, FolderUpdate

router = APIRouter(prefix="/folders", tags=["folders"])


async def _owned_folder(db, user, folder_id: uuid.UUID) -> Folder:
    folder = await db.get(Folder, folder_id)
    if folder is None or folder.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
    return folder


@router.get("", response_model=list[FolderOut])
async def list_folders(user: CurrentUser, db: DB) -> list[FolderOut]:
    counts = dict(
        (
            await db.execute(
                select(Note.folder_id, func.count(Note.id))
                .where(Note.owner_id == user.id, Note.deleted_at.is_(None))
                .group_by(Note.folder_id)
            )
        ).all()
    )
    result = await db.execute(
        select(Folder)
        .where(Folder.owner_id == user.id)
        .order_by(Folder.position, Folder.name)
    )
    out = []
    for f in result.scalars():
        item = FolderOut.model_validate(f)
        item.note_count = counts.get(f.id, 0)
        out.append(item)
    return out


@router.post("", response_model=FolderOut, status_code=201)
async def create_folder(payload: FolderCreate, user: CurrentUser, db: DB) -> FolderOut:
    if payload.parent_id is not None:
        await _owned_folder(db, user, payload.parent_id)
    max_pos = (
        await db.execute(
            select(func.coalesce(func.max(Folder.position), -1)).where(
                Folder.owner_id == user.id
            )
        )
    ).scalar_one()
    folder = Folder(
        owner_id=user.id,
        name=payload.name.strip(),
        parent_id=payload.parent_id,
        position=max_pos + 1,
    )
    db.add(folder)
    await db.flush()
    return FolderOut.model_validate(folder)


@router.patch("/{folder_id}", response_model=FolderOut)
async def update_folder(
    folder_id: uuid.UUID, payload: FolderUpdate, user: CurrentUser, db: DB
) -> FolderOut:
    folder = await _owned_folder(db, user, folder_id)
    if folder.is_default and payload.name is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The default folder cannot be renamed",
        )
    if payload.name is not None:
        folder.name = payload.name.strip()
    if payload.parent_id is not None:
        if payload.parent_id == folder.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A folder cannot be its own parent",
            )
        await _owned_folder(db, user, payload.parent_id)
        folder.parent_id = payload.parent_id
    if payload.position is not None:
        folder.position = payload.position
    await db.flush()
    return FolderOut.model_validate(folder)


@router.delete("/{folder_id}", status_code=204)
async def delete_folder(folder_id: uuid.UUID, user: CurrentUser, db: DB) -> None:
    folder = await _owned_folder(db, user, folder_id)
    if folder.is_default:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The default folder cannot be deleted",
        )
    # Deleting a folder never deletes its notes: everything inside —
    # including nested subfolders — moves to the default "Notes" folder,
    # alive. (Notes already in the trash just change their home folder.)
    # Without this walk, the DB cascade would HARD-delete subfolder notes.
    default = (
        await db.execute(
            select(Folder).where(Folder.owner_id == user.id, Folder.is_default.is_(True))
        )
    ).scalar_one()
    doomed_ids = [folder.id]
    frontier = [folder.id]
    while frontier:
        children = (
            (await db.execute(select(Folder.id).where(Folder.parent_id.in_(frontier))))
            .scalars()
            .all()
        )
        frontier = children
        doomed_ids.extend(children)
    notes = (
        await db.execute(select(Note).where(Note.folder_id.in_(doomed_ids)))
    ).scalars()
    for note in notes:
        note.folder_id = default.id
    await db.flush()
    # Delete deepest-first explicitly — don't lean on DB cascade behavior.
    for fid in reversed(doomed_ids):
        doomed = await db.get(Folder, fid)
        if doomed is not None:
            await db.delete(doomed)

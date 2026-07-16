"""Admin-only user management.

There is no email in this system, so the admin IS the account-recovery
path: they create accounts, reset passwords, and clear a lost 2FA setup.
"""

import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.config import settings
from app.core.deps import DB, get_current_user
from app.core.security import hash_password, password_error
from app.models import Folder, Session, TotpRecoveryCode, User
from app.schemas.auth import RegisterRequest, UserOut
from app.services.restore import (
    RestoreError,
    extract_media,
    restore_database,
    run_migrations,
)

router = APIRouter(prefix="/admin", tags=["admin"])


async def get_admin_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )
    return user


AdminUser = Annotated[User, Depends(get_admin_user)]


class AdminUserOut(UserOut):
    created_at: datetime


class AdminUserCreate(RegisterRequest):
    """Same shape/validation as registration; created by an admin."""


class PasswordReset(BaseModel):
    password: str = Field(min_length=1, max_length=200)


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(admin: AdminUser, db: DB) -> list[AdminUserOut]:
    result = await db.execute(select(User).order_by(User.created_at))
    return [AdminUserOut.model_validate(u) for u in result.scalars()]


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(payload: AdminUserCreate, admin: AdminUser, db: DB) -> UserOut:
    if err := password_error(payload.password, settings.min_password_length):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=err)
    existing = await db.execute(select(User).where(User.username == payload.username))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="That username is taken"
        )
    user = User(
        username=payload.username,
        name=payload.name.strip(),
        password_hash=hash_password(payload.password),
        is_admin=False,
    )
    db.add(user)
    await db.flush()
    db.add(Folder(owner_id=user.id, name="Notes", is_default=True, position=0))
    return UserOut.model_validate(user)


async def _target_user(db, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.post("/users/{user_id}/password", status_code=204)
async def reset_password(
    user_id: uuid.UUID, payload: PasswordReset, admin: AdminUser, db: DB
) -> None:
    if err := password_error(payload.password, settings.min_password_length):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=err)
    user = await _target_user(db, user_id)
    user.password_hash = hash_password(payload.password)
    # An admin reset invalidates every existing session for that account.
    await db.execute(delete(Session).where(Session.user_id == user.id))


@router.post("/users/{user_id}/totp/disable", status_code=204)
async def disable_user_totp(user_id: uuid.UUID, admin: AdminUser, db: DB) -> None:
    """Recovery path for a lost authenticator + recovery codes."""
    user = await _target_user(db, user_id)
    user.totp_enabled = False
    user.totp_secret = None
    await db.execute(
        delete(TotpRecoveryCode).where(TotpRecoveryCode.user_id == user.id)
    )


class ImportNote(BaseModel):
    title: str = Field(default="", max_length=400)
    body: dict | None = None
    body_text: str = ""
    created_at: datetime
    updated_at: datetime
    pinned: bool = False


class ImportRequest(BaseModel):
    folder_name: str = Field(min_length=1, max_length=200)
    notes: list[ImportNote] = Field(max_length=1000)


@router.post("/import", status_code=201)
async def bulk_import(payload: ImportRequest, admin: AdminUser, db: DB) -> dict:
    """Migration endpoint: insert notes with their ORIGINAL timestamps into
    a (created-on-demand) folder owned by the admin. Tags sync from
    body_text as usual, so inline #hashtags become real tags."""
    from app.models import Note
    from app.services.revisions import record_revision
    from app.services.tags import sweep_orphan_tags, sync_note_tags

    folder = (
        await db.execute(
            select(Folder).where(
                Folder.owner_id == admin.id, Folder.name == payload.folder_name
            )
        )
    ).scalar_one_or_none()
    if folder is None:
        folder = Folder(owner_id=admin.id, name=payload.folder_name)
        db.add(folder)
        await db.flush()

    for item in payload.notes:
        note = Note(
            owner_id=admin.id,
            folder_id=folder.id,
            title=item.title,
            body=item.body,
            body_text=item.body_text,
            pinned=item.pinned,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        db.add(note)
        await db.flush()
        await sync_note_tags(db, note, admin.id, sweep_orphans=False)
        await record_revision(db, note, admin.id)

    await sweep_orphan_tags(db, admin.id)
    return {"imported": len(payload.notes), "folder_id": str(folder.id)}


@router.post("/restore")
async def restore_from_backup(
    admin: AdminUser,
    db: DB,
    db_dump: Annotated[UploadFile, File()],
    media_archive: Annotated[UploadFile | None, File()] = None,
) -> dict:
    """Replace this server's ENTIRE contents with a nightly backup pair
    (db-*.dump + media-*.tar.gz). The dump restores inside a single
    transaction, so a bad file changes nothing. Every session is part of
    the backup, so all clients — including this one — sign in again."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="restore-"))
    try:
        # pg_restore needs real files on disk, not upload streams.
        dump_path = tmp_dir / "db.dump"
        with dump_path.open("wb") as out:
            shutil.copyfileobj(db_dump.file, out)
        media_path: Path | None = None
        if media_archive is not None and media_archive.filename:
            media_path = tmp_dir / "media.tar.gz"
            with media_path.open("wb") as out:
                shutil.copyfileobj(media_archive.file, out)

        # Release every pooled connection — pg_restore is about to drop
        # the tables they're parked on.
        from app.database import engine

        await db.close()
        await engine.dispose()

        await restore_database(dump_path)
        await run_migrations()
        media_files = extract_media(media_path) if media_path else 0
    except RestoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return {"restored": True, "media_files": media_files}


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: uuid.UUID, admin: AdminUser, db: DB) -> None:
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account",
        )
    user = await _target_user(db, user_id)
    # Notes, folders, sessions, tags, and attachment rows cascade with the
    # user; uploaded files linger on the media volume until a future GC.
    await db.delete(user)

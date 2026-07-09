"""Access resolution for shared notes and folders.

Effective role on a note: owner > direct note share > folder share on the
note's current folder. Notes in Recently Deleted are visible only to their
owner. Roles: "owner" | "editor" | "viewer" | None (no access).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FolderShare, Note, NoteShare, User


def resolved_role(
    note: Note,
    user_id: uuid.UUID,
    note_roles: dict[uuid.UUID, str],
    folder_roles: dict[uuid.UUID, str],
) -> str | None:
    """Effective role from prefetched share maps (see share_maps): owner
    wins, then a direct note share, then a share on the note's folder."""
    if note.owner_id == user_id:
        return "owner"
    return note_roles.get(note.id) or folder_roles.get(note.folder_id)


async def note_role(db: AsyncSession, user: User, note: Note) -> str | None:
    if note.owner_id == user.id:
        return "owner"
    if note.deleted_at is not None:
        return None  # the owner's trash is theirs alone
    if note.locked:
        return None  # locked notes are owner-only, even inside shared folders

    direct = (
        await db.execute(
            select(NoteShare.role).where(NoteShare.note_id == note.id, NoteShare.user_id == user.id)
        )
    ).scalar_one_or_none()
    if direct is not None:
        return direct  # a direct share overrides the folder share

    via_folder = (
        await db.execute(
            select(FolderShare.role).where(
                FolderShare.folder_id == note.folder_id,
                FolderShare.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    return via_folder


async def share_maps(
    db: AsyncSession, user: User
) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, str]]:
    """(note_id → role, folder_id → role) for everything shared with user."""
    note_roles = dict(
        (
            await db.execute(
                select(NoteShare.note_id, NoteShare.role).where(NoteShare.user_id == user.id)
            )
        ).all()
    )
    folder_roles = dict(
        (
            await db.execute(
                select(FolderShare.folder_id, FolderShare.role).where(
                    FolderShare.user_id == user.id
                )
            )
        ).all()
    )
    return note_roles, folder_roles

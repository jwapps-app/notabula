"""Server-side note search — my notes AND notes shared with me.

On Postgres this uses the generated tsvector column (see migration 0002)
with a prefix-friendly tsquery so results appear as the user types
("groc" matches "Groceries"). On other dialects (SQLite in tests) it
falls back to a case-insensitive substring match. Results reached via a
share are annotated with the role and owner, like the shared list view.
"""

import re

from fastapi import APIRouter, Query
from sqlalchemy import or_, select, text
from sqlalchemy.orm import defer

from app.core.deps import DB, CurrentUser
from app.models import Note, User
from app.routers.notes import _list_item
from app.schemas.note import NoteListItem
from app.services.access import resolved_role, share_maps

router = APIRouter(prefix="/search", tags=["search"])

MAX_RESULTS = 100


def _prefix_tsquery(q: str) -> str | None:
    """Build a sanitized `word:* & word:*` tsquery from raw user input."""
    terms = re.findall(r"\w+", q, re.UNICODE)
    if not terms:
        return None
    return " & ".join(f"{t}:*" for t in terms[:8])


@router.get("", response_model=list[NoteListItem])
async def search_notes(
    user: CurrentUser,
    db: DB,
    q: str = Query(min_length=1, max_length=200),
) -> list[NoteListItem]:
    note_roles, folder_roles = await share_maps(db, user)
    accessible = or_(
        Note.owner_id == user.id,
        Note.id.in_(note_roles.keys()) if note_roles else False,
        Note.folder_id.in_(folder_roles.keys()) if folder_roles else False,
    )

    if db.get_bind().dialect.name == "postgresql":
        tsquery = _prefix_tsquery(q)
        if tsquery is None:
            return []
        result = await db.execute(
            select(Note, User.name)
            .options(defer(Note.body), defer(Note.cipher_body))
            .join(User, User.id == Note.owner_id)
            .where(
                accessible,
                Note.deleted_at.is_(None),
                text("search_vector @@ to_tsquery('english', :tsq)"),
            )
            .order_by(
                text("ts_rank(search_vector, to_tsquery('english', :tsq)) DESC"),
                Note.updated_at.desc(),
            )
            .limit(MAX_RESULTS)
            .params(tsq=tsquery)
        )
    else:
        like = f"%{q}%"
        result = await db.execute(
            select(Note, User.name)
            .options(defer(Note.body), defer(Note.cipher_body))
            .join(User, User.id == Note.owner_id)
            .where(
                accessible,
                Note.deleted_at.is_(None),
                or_(Note.title.ilike(like), Note.body_text.ilike(like)),
            )
            .order_by(Note.updated_at.desc())
            .limit(MAX_RESULTS)
        )

    items = []
    for note, owner_name in result.all():
        if note.owner_id == user.id:
            items.append(_list_item(note))
        else:
            role = resolved_role(note, user.id, note_roles, folder_roles)
            items.append(_list_item(note, role=role, owner_name=owner_name))
    return items

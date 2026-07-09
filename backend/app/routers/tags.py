"""Tag listing and renaming.

Tags are derived from #hashtags in note text, so renaming a tag means
rewriting the hashtag in every note that contains it — body, plain text,
and title — like a project-wide find-and-replace scoped to the tag.
"""

import re
import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import attributes

from app.core.deps import DB, CurrentUser
from app.models import Note, Tag, note_tags
from app.services.revisions import record_revision
from app.services.tags import sync_note_tags

router = APIRouter(prefix="/tags", tags=["tags"])

# Same shape the tag extractor accepts: word chars + hyphens, ≥1 letter.
_VALID_TAG = re.compile(r"^[\w-]*[^\W\d_][\w-]*$")


class TagOut(BaseModel):
    id: uuid.UUID
    name: str
    note_count: int


class TagRenameRequest(BaseModel):
    new_name: str = Field(min_length=1, max_length=100)

    @field_validator("new_name")
    @classmethod
    def validate_tag(cls, v: str) -> str:
        v = v.strip().lstrip("#")
        if not _VALID_TAG.fullmatch(v):
            raise ValueError("Tags may use letters, numbers, _ and - (at least one letter)")
        return v


@router.get("", response_model=list[TagOut])
async def list_tags(user: CurrentUser, db: DB) -> list[TagOut]:
    result = await db.execute(
        select(Tag.id, Tag.name, func.count(Note.id))
        .join(note_tags, note_tags.c.tag_id == Tag.id)
        .join(Note, Note.id == note_tags.c.note_id)
        .where(Tag.owner_id == user.id, Note.deleted_at.is_(None))
        .group_by(Tag.id, Tag.name)
        .order_by(Tag.name)
    )
    return [
        TagOut(id=tid, name=name, note_count=count)
        for tid, name, count in result.all()
        if count > 0
    ]


def _rewrite_doc(node, pattern: re.Pattern, replacement: str) -> bool:
    """Replace the hashtag inside ProseMirror text nodes, in place."""
    changed = False
    if isinstance(node, dict):
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            new_text = pattern.sub(replacement, node["text"])
            if new_text != node["text"]:
                node["text"] = new_text
                changed = True
        for child in node.get("content") or []:
            changed = _rewrite_doc(child, pattern, replacement) or changed
    elif isinstance(node, list):
        for child in node:
            changed = _rewrite_doc(child, pattern, replacement) or changed
    return changed


@router.post("/{name}/rename", status_code=200)
async def rename_tag(
    name: str, payload: TagRenameRequest, user: CurrentUser, db: DB
) -> dict:
    """Rewrite #old → #new in every note of mine that carries the tag —
    including notes sitting in Recently Deleted, so a restore stays
    consistent. Locked notes can't be rewritten (no plaintext on the
    server); they keep the old hashtag until unlocked and edited."""
    old = name.strip().lstrip("#").lower()
    new = payload.new_name
    if old == new.lower():
        return {"updated": 0}

    # Whole-tag, case-insensitive: #Old matches, #older does not.
    pattern = re.compile(rf"#{re.escape(old)}(?![\w-])", re.IGNORECASE)

    tag = (
        await db.execute(
            select(Tag).where(Tag.owner_id == user.id, Tag.name == old)
        )
    ).scalar_one_or_none()
    if tag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")

    notes = (
        (
            await db.execute(
                select(Note)
                .join(note_tags, note_tags.c.note_id == Note.id)
                .where(note_tags.c.tag_id == tag.id, Note.locked.is_(False))
            )
        )
        .scalars()
        .all()
    )

    updated = 0
    for note in notes:
        body = note.body
        body_changed = _rewrite_doc(body, pattern, f"#{new}") if body else False
        if body_changed:
            note.body = body
            # JSONB columns only persist when SQLAlchemy sees a new value.
            attributes.flag_modified(note, "body")
        new_text = pattern.sub(f"#{new}", note.body_text or "")
        text_changed = new_text != note.body_text
        if text_changed:
            note.body_text = new_text
        new_title = pattern.sub(f"#{new}", note.title or "")
        if new_title != note.title:
            note.title = new_title
        if body_changed or text_changed:
            note.version += 1
            await sync_note_tags(db, note, user.id)
            await record_revision(db, note, user.id)
            updated += 1

    return {"updated": updated}

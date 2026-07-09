"""Tag extraction and sync.

Tags are #hashtags typed anywhere in a note's text (iOS Notes model).
On every note save the server re-derives the tag set from body_text,
creates missing Tag rows, relinks the note, and removes the owner's
now-orphaned tags.
"""

import re
import uuid

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Note, Tag, note_tags

# A word run following '#', letters/digits/underscore/hyphen; must contain
# at least one letter so "#123" or "# " don't become tags.
_TAG_RE = re.compile(r"#([\w-]*[^\W\d_][\w-]*)", re.UNICODE)

MAX_TAGS_PER_NOTE = 50


def extract_tag_names(text: str) -> set[str]:
    return {m.group(1).lower() for m in _TAG_RE.finditer(text or "")}


async def sweep_orphan_tags(db: AsyncSession, owner_id: uuid.UUID) -> None:
    """Drop the owner's tags that no longer appear on any note."""
    await db.execute(
        delete(Tag).where(
            Tag.owner_id == owner_id,
            ~Tag.id.in_(select(note_tags.c.tag_id)),
        )
    )


async def sync_note_tags(
    db: AsyncSession, note: Note, owner_id: uuid.UUID, *, sweep_orphans: bool = True
) -> None:
    """Make the note's tag links match the #hashtags in body_text.

    Works on the note_tags table directly (not the ORM collection) because
    collection assignment would lazy-load, which async engines forbid.

    Pass sweep_orphans=False inside bulk loops (import, tag rename) and call
    sweep_orphan_tags once at the end — otherwise the orphan cleanup runs
    once per note for no gain.
    """
    names = sorted(extract_tag_names(note.body_text))[:MAX_TAGS_PER_NOTE]

    tags: list[Tag] = []
    if names:
        existing = {
            t.name: t
            for t in (
                await db.execute(
                    select(Tag).where(Tag.owner_id == owner_id, Tag.name.in_(names))
                )
            ).scalars()
        }
        new = []
        for name in names:
            tag = existing.get(name)
            if tag is None:
                tag = Tag(owner_id=owner_id, name=name)
                db.add(tag)
                new.append(tag)
            tags.append(tag)
        if new:
            await db.flush()  # assign ids to freshly created tags

    await db.execute(delete(note_tags).where(note_tags.c.note_id == note.id))
    if tags:
        await db.execute(
            insert(note_tags),
            [{"note_id": note.id, "tag_id": t.id} for t in tags],
        )

    # Drop the owner's tags that no longer appear on any note.
    await db.execute(
        delete(Tag).where(
            Tag.owner_id == owner_id,
            ~Tag.id.in_(select(note_tags.c.tag_id)),
        )
    )

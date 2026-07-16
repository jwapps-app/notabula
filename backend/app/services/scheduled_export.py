"""Nightly per-user export — the automated escape hatch.

Writes <MEDIA_ROOT>/exports/<username>.zip containing every non-deleted
note as plain-text markdown (organized by folder) plus a lossless
notes.json. The zips live on the media volume, so the existing nightly
backup tarball carries them offsite automatically. Locked notes export
their ciphertext in notes.json (title-only in markdown) — the server
never has their plaintext.

Users whose notes haven't changed since the last run are skipped (their
zip is already current), and zip building/writing is offloaded to a
thread so the event loop isn't blocked by compression.
"""

import asyncio
import io
import json
import logging
import re
import zipfile
from datetime import timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Folder, Note, User

logger = logging.getLogger(__name__)


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^\w \-]+", "", text, flags=re.UNICODE).strip()
    return re.sub(r"\s+", " ", cleaned)[:60] or "untitled"


def _build_zip(folders: dict, payload: list[dict], notes_md: list[tuple[str, str]]) -> bytes:
    """Blocking zip assembly — runs in a worker thread."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("notes.json", json.dumps(payload, indent=1))
        for name, content in notes_md:
            zf.writestr(name, content)
    return buffer.getvalue()


async def write_user_exports(db: AsyncSession) -> int:
    exports_dir = Path(settings.media_root) / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    users = (await db.execute(select(User))).scalars().all()
    written = 0
    for user in users:
        target = exports_dir / f"{user.username}.zip"

        # Skip users with no changes since their zip was written — rebuilding
        # every user's full archive nightly is wasted CPU and disk churn.
        latest = (
            await db.execute(
                select(func.max(Note.updated_at)).where(Note.owner_id == user.id)
            )
        ).scalar_one_or_none()
        if target.exists() and latest is not None:
            zip_mtime = target.stat().st_mtime
            if latest.replace(tzinfo=timezone.utc).timestamp() < zip_mtime:
                continue

        folders = {
            f.id: f.name
            for f in (
                await db.execute(select(Folder).where(Folder.owner_id == user.id))
            ).scalars()
        }
        notes = (
            (
                await db.execute(
                    select(Note).where(
                        Note.owner_id == user.id, Note.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )

        payload = [
            {
                "id": str(n.id),
                "folder": folders.get(n.folder_id, "Notes"),
                "title": n.title,
                "body": n.body,
                "body_text": n.body_text,
                "locked": n.locked,
                "cipher_body": n.cipher_body,
                "pinned": n.pinned,
                "created_at": n.created_at.isoformat(),
                "updated_at": n.updated_at.isoformat(),
            }
            for n in notes
        ]
        notes_md = []
        for n in notes:
            folder = _slug(folders.get(n.folder_id, "Notes"))
            name = f"{folder}/{_slug(n.title)}-{str(n.id)[:8]}.md"
            content = n.body_text if not n.locked else f"{n.title}\n\n[locked note]"
            notes_md.append((name, content or n.title or ""))

        data = await asyncio.to_thread(_build_zip, folders, payload, notes_md)
        await asyncio.to_thread(target.write_bytes, data)
        written += 1

    return written

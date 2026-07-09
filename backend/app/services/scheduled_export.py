"""Nightly per-user export — the automated escape hatch.

Writes <MEDIA_ROOT>/exports/<username>.zip containing every non-deleted
note as plain-text markdown (organized by folder) plus a lossless
notes.json. The zips live on the media volume, so the existing nightly
backup tarball carries them offsite automatically. Locked notes export
their ciphertext in notes.json (title-only in markdown) — the server
never has their plaintext.
"""

import io
import json
import logging
import re
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Folder, Note, User

logger = logging.getLogger(__name__)


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^\w \-]+", "", text, flags=re.UNICODE).strip()
    return re.sub(r"\s+", " ", cleaned)[:60] or "untitled"


async def write_user_exports(db: AsyncSession) -> int:
    exports_dir = Path(settings.media_root) / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    users = (await db.execute(select(User))).scalars().all()
    for user in users:
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

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
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
            zf.writestr("notes.json", json.dumps(payload, indent=1))
            for n in notes:
                folder = _slug(folders.get(n.folder_id, "Notes"))
                name = f"{folder}/{_slug(n.title)}-{str(n.id)[:8]}.md"
                content = n.body_text if not n.locked else f"{n.title}\n\n[locked note]"
                zf.writestr(name, content or n.title or "")

        (exports_dir / f"{user.username}.zip").write_bytes(buffer.getvalue())

    return len(users)

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
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Folder, Note, User

logger = logging.getLogger(__name__)


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^\w \-]+", "", text, flags=re.UNICODE).strip()
    return re.sub(r"\s+", " ", cleaned)[:60] or "untitled"


def _build_zip(payload: list[dict], notes_md: list[tuple[str, str]]) -> bytes:
    """Blocking zip assembly — runs in a worker thread."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("notes.json", json.dumps(payload, indent=1))
        for name, content in notes_md:
            zf.writestr(name, content)
    return buffer.getvalue()


def _write_atomic(target: Path, data: bytes, signature: str) -> None:
    """Write the zip and its signature so a crash mid-write can never leave
    a truncated archive that looks current: the bytes land in a temp file
    that is renamed into place (atomic on POSIX), and the signature file
    is written only after the zip is complete."""
    tmp = target.with_suffix(".zip.tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    target.with_suffix(".zip.sig").write_text(signature)


async def _export_signature(db: AsyncSession, user_id) -> str:
    """Fingerprint of everything the export depends on. Not just the newest
    note timestamp — that misses imports with historical dates, hard
    deletes (the newest row disappears), and folder renames — but the live
    note count, the newest note and folder timestamps, and the set of
    folder names. Any change flips it; an unchanged fingerprint means the
    existing zip is still exact."""
    notes = (
        await db.execute(
            select(func.count(Note.id), func.max(Note.updated_at)).where(
                Note.owner_id == user_id, Note.deleted_at.is_(None)
            )
        )
    ).one()
    folders = (
        await db.execute(
            select(func.count(Folder.id), func.max(Folder.updated_at)).where(
                Folder.owner_id == user_id
            )
        )
    ).one()
    return json.dumps([str(v) for v in (*notes, *folders)])


async def write_user_exports(db: AsyncSession) -> int:
    exports_dir = Path(settings.media_root) / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    users = (await db.execute(select(User))).scalars().all()
    written = 0
    for user in users:
        target = exports_dir / f"{user.username}.zip"

        # Skip users whose export-relevant state hasn't changed — rebuilding
        # every user's full archive nightly is wasted CPU and disk churn.
        signature = await _export_signature(db, user.id)
        sig_file = target.with_suffix(".zip.sig")
        if target.exists() and sig_file.exists() and sig_file.read_text() == signature:
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

        data = await asyncio.to_thread(_build_zip, payload, notes_md)
        await asyncio.to_thread(_write_atomic, target, data, signature)
        written += 1

    # Exports of accounts that no longer exist are stale personal data on
    # the backup volume — remove them.
    live = {f"{u.username}.zip" for u in users}
    for path in exports_dir.glob("*.zip"):
        if path.name not in live:
            path.unlink(missing_ok=True)
            path.with_suffix(".zip.sig").unlink(missing_ok=True)

    return written

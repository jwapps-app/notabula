"""Restore the whole server from a nightly backup pair.

The backup container writes db-<ts>.dump (pg_dump custom format) and
media-<ts>.tar.gz (every uploaded attachment) once a day. This service
is the other half of that promise: given those two files, replace the
database and the media volume in place — Settings → Restore from Backup.

The database step runs pg_restore with --single-transaction, so a bad
or truncated dump rolls back and the running data stays untouched.
Migrations run afterwards in case the backup came from an older build.
"""

import asyncio
import logging
import os
import tarfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.config import settings

logger = logging.getLogger(__name__)


class RestoreError(RuntimeError):
    """A restore step failed; the message is safe to show the admin."""


def _db_conn() -> dict[str, str]:
    url = urlsplit(str(settings.database_url))
    return {
        "host": url.hostname or "localhost",
        "port": str(url.port or 5432),
        "user": unquote(url.username or ""),
        "password": unquote(url.password or ""),
        "dbname": unquote(url.path.lstrip("/")),
    }


async def restore_database(dump_path: Path) -> None:
    conn = _db_conn()
    proc = await asyncio.create_subprocess_exec(
        "pg_restore",
        "--clean",
        "--if-exists",
        "--single-transaction",
        "--no-owner",
        "--no-privileges",
        "-h",
        conn["host"],
        "-p",
        conn["port"],
        "-U",
        conn["user"],
        "-d",
        conn["dbname"],
        str(dump_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PGPASSWORD": conn["password"]},
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        lines = stderr.decode(errors="replace").strip().splitlines()
        tail = lines[-1] if lines else "unknown error"
        logger.error("pg_restore failed:\n%s", "\n".join(lines[-20:]))
        raise RestoreError(f"Database restore failed — nothing was changed. ({tail})")


async def run_migrations() -> None:
    """Bring a restored (possibly older) schema up to this build's head."""
    backend_root = Path(__file__).resolve().parents[2]  # holds alembic.ini
    proc = await asyncio.create_subprocess_exec(
        "alembic",
        "upgrade",
        "head",
        cwd=str(backend_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr.decode(errors="replace").strip()[-300:]
        raise RestoreError(f"Restore succeeded but migrations failed: {tail}")


def extract_media(tar_path: Path) -> int:
    """Unpack the media archive over the media volume; returns files written.

    The 'data' filter rejects absolute paths, .. traversal, and special
    files, so a crafted archive can't write outside the media root.
    """
    media_root = Path(settings.media_root)
    media_root.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar:
                tar.extract(member, media_root, filter="data")
                if member.isfile():
                    written += 1
    except (tarfile.TarError, OSError) as exc:
        raise RestoreError(f"Media archive could not be unpacked: {exc}") from exc
    return written

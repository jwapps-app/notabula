"""Restore-from-backup endpoint and media extraction safety.

The real pg_restore path needs a Postgres server, so the endpoint tests
stub the subprocess-driven steps and assert the orchestration; the tar
handling is exercised for real, including the path-traversal guard.
"""

import io
import tarfile

import pytest

from app.services.restore import RestoreError, extract_media


def _tar_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extract_media_writes_files(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    archive = tmp_path / "media.tar.gz"
    archive.write_bytes(
        _tar_bytes({"./attachments/pic.jpg": b"jpeg!", "./exports/x.zip": b"zip!"})
    )

    written = extract_media(archive)

    assert written == 2
    assert (tmp_path / "media" / "attachments" / "pic.jpg").read_bytes() == b"jpeg!"


def test_extract_media_rejects_traversal(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    archive = tmp_path / "evil.tar.gz"
    archive.write_bytes(_tar_bytes({"../outside.txt": b"gotcha"}))

    with pytest.raises(RestoreError):
        extract_media(archive)
    assert not (tmp_path / "outside.txt").exists()


async def test_restore_requires_admin(auth):
    from tests.conftest import make_user

    client, admin_headers, _ = auth
    bob = await make_user(client, admin_headers)  # not an admin
    resp = await client.post(
        "/api/v1/admin/restore",
        headers=bob,
        files={"db_dump": ("db.dump", b"not-a-real-dump")},
    )
    assert resp.status_code == 403


async def test_restore_orchestration(auth, tmp_path, monkeypatch):
    """Admin upload runs the three steps in order and reports media count."""
    import app.routers.admin as admin_router
    from app.config import settings

    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    calls: list[str] = []

    async def fake_restore(dump_path):
        calls.append("db")
        assert dump_path.read_bytes() == b"fake-dump"

    async def fake_migrations():
        calls.append("migrate")

    monkeypatch.setattr(admin_router, "restore_database", fake_restore)
    monkeypatch.setattr(admin_router, "run_migrations", fake_migrations)

    client, admin_headers, _ = auth
    resp = await client.post(
        "/api/v1/admin/restore",
        headers=admin_headers,
        files={
            "db_dump": ("db-20260708.dump", b"fake-dump"),
            "media_archive": (
                "media-20260708.tar.gz",
                _tar_bytes({"./attachments/a.jpg": b"img"}),
            ),
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {"restored": True, "media_files": 1}
    assert calls == ["db", "migrate"]
    assert (tmp_path / "media" / "attachments" / "a.jpg").exists()


async def test_restore_failure_reports_error(auth, monkeypatch):
    import app.routers.admin as admin_router

    async def fake_restore(dump_path):
        raise RestoreError("Database restore failed — nothing was changed.")

    monkeypatch.setattr(admin_router, "restore_database", fake_restore)

    client, admin_headers, _ = auth
    resp = await client.post(
        "/api/v1/admin/restore",
        headers=admin_headers,
        files={"db_dump": ("db.dump", b"garbage")},
    )
    assert resp.status_code == 500
    assert "nothing was changed" in resp.json()["detail"]

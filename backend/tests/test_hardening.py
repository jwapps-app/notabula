"""API hardening: own password change, shared search, throttle, attachment GC."""

from datetime import datetime, timedelta, timezone

from tests.conftest import make_user


async def _default_folder_id(client, headers):
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    return next(f["id"] for f in folders if f["is_default"])


# --- Change own password --------------------------------------------------


async def test_change_own_password(auth):
    client, alice, _ = auth
    # wrong current password refused
    assert (
        await client.post(
            "/api/v1/auth/password",
            headers=alice,
            json={"current_password": "nope", "new_password": "newpassword9"},
        )
    ).status_code == 401

    # a second session (another device) exists
    other = await client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "password123"}
    )
    other_headers = {"Authorization": f"Bearer {other.json()['session_token']}"}

    resp = await client.post(
        "/api/v1/auth/password",
        headers=alice,
        json={"current_password": "password123", "new_password": "newpassword9"},
    )
    assert resp.status_code == 204

    # this session survives; the other device is signed out
    assert (await client.get("/api/v1/auth/me", headers=alice)).status_code == 200
    assert (
        await client.get("/api/v1/auth/me", headers=other_headers)
    ).status_code == 401

    # old password dead, new one works
    assert (
        await client.post(
            "/api/v1/auth/login", json={"username": "alice", "password": "password123"}
        )
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login", json={"username": "alice", "password": "newpassword9"}
        )
    ).status_code == 200


# --- Search covers shared notes -------------------------------------------


async def test_search_includes_shared_notes(auth):
    client, alice, _ = auth
    bob = await make_user(client, alice)
    fid = await _default_folder_id(client, alice)
    note = (
        await client.post(
            "/api/v1/notes",
            headers=alice,
            json={"folder_id": fid, "title": "Casserole", "body_text": "Casserole\npaprika"},
        )
    ).json()

    # not shared yet → bob finds nothing
    assert (await client.get("/api/v1/search?q=paprika", headers=bob)).json() == []

    await client.put(
        f"/api/v1/notes/{note['id']}/shares",
        headers=alice,
        json={"username": "bob", "role": "viewer"},
    )
    hits = (await client.get("/api/v1/search?q=paprika", headers=bob)).json()
    assert [(h["title"], h["role"], h["owner_name"]) for h in hits] == [
        ("Casserole", "viewer", "Alice")
    ]

    # alice's own results stay unannotated
    mine = (await client.get("/api/v1/search?q=paprika", headers=alice)).json()
    assert mine[0]["role"] == "owner"


# --- Login throttling -------------------------------------------------------


async def test_login_throttle_locks_after_failures(auth):
    client, _, _ = auth
    for _i in range(5):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "wrong-password"},
        )
        assert resp.status_code == 401
    # 6th attempt — even with the CORRECT password — is throttled
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "password123"}
    )
    assert resp.status_code == 429


async def test_login_throttle_clears_on_success(auth):
    client, _, _ = auth
    for _i in range(3):
        await client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "wrong-password"},
        )
    assert (
        await client.post(
            "/api/v1/auth/login", json={"username": "alice", "password": "password123"}
        )
    ).status_code == 200
    # counter reset — three more failures don't lock
    for _i in range(3):
        await client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "wrong-password"},
        )
    assert (
        await client.post(
            "/api/v1/auth/login", json={"username": "alice", "password": "password123"}
        )
    ).status_code == 200


# --- Attachment GC ----------------------------------------------------------

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcff9fa11e0000078400816cd42ca70000000049454e44ae426082"
)


async def _upload(client, headers, tmp_media):
    resp = await client.post(
        "/api/v1/attachments",
        headers=headers,
        files={"file": ("p.png", PNG, "image/png")},
    )
    assert resp.status_code == 201
    return resp.json()


async def _age_attachments():
    from sqlalchemy import update

    from app.database import get_db
    from app.main import app
    from app.models import Attachment

    agen = app.dependency_overrides[get_db]()
    db = await anext(agen)
    old = datetime.now(timezone.utc) - timedelta(days=2)
    await db.execute(update(Attachment).values(created_at=old, updated_at=old))
    await db.commit()
    await agen.aclose()


async def test_orphan_attachments_purged_referenced_kept(auth, tmp_path, monkeypatch):
    from app.config import settings
    from app.database import get_db
    from app.main import app
    from app.services.attachment_gc import purge_orphan_attachments

    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    client, alice, _ = auth
    fid = await _default_folder_id(client, alice)

    orphan = await _upload(client, alice, tmp_path)
    used = await _upload(client, alice, tmp_path)

    # reference `used` in a note body
    await client.post(
        "/api/v1/notes",
        headers=alice,
        json={
            "folder_id": fid,
            "title": "Pic",
            "body": {
                "type": "doc",
                "content": [{"type": "image", "attrs": {"src": used["url"]}}],
            },
            "body_text": "Pic",
        },
    )

    await _age_attachments()

    agen = app.dependency_overrides[get_db]()
    db = await anext(agen)
    removed = await purge_orphan_attachments(db)
    await db.commit()
    await agen.aclose()

    assert removed == 1
    orphan_file = tmp_path / "attachments" / orphan["url"].rsplit("/", 1)[1]
    used_file = tmp_path / "attachments" / used["url"].rsplit("/", 1)[1]
    assert not orphan_file.exists()
    assert used_file.exists()


async def test_fresh_uploads_survive_gc(auth, tmp_path, monkeypatch):
    from app.config import settings
    from app.database import get_db
    from app.main import app
    from app.services.attachment_gc import purge_orphan_attachments

    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    client, alice, _ = auth
    fresh = await _upload(client, alice, tmp_path)  # just uploaded, unreferenced

    agen = app.dependency_overrides[get_db]()
    db = await anext(agen)
    removed = await purge_orphan_attachments(db)
    await db.commit()
    await agen.aclose()

    assert removed == 0  # grace period protects it
    assert (tmp_path / "attachments" / fresh["url"].rsplit("/", 1)[1]).exists()


async def test_unclaimed_files_swept_from_disk(auth, tmp_path, monkeypatch):
    """Files with no attachment row (e.g. left behind after a user deletion
    cascaded the rows away) are removed once past the grace period."""
    import os

    from app.config import settings
    from app.database import get_db
    from app.main import app
    from app.services.attachment_gc import purge_orphan_attachments

    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    media_dir = tmp_path / "attachments"
    media_dir.mkdir()

    stranded = media_dir / "deadbeef000000000000000000000000.jpg"
    stranded.write_bytes(b"stranded")
    two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    os.utime(stranded, (two_days_ago, two_days_ago))

    fresh = media_dir / "cafebabe000000000000000000000000.jpg"
    fresh.write_bytes(b"fresh")  # unclaimed but inside the grace period

    agen = app.dependency_overrides[get_db]()
    db = await anext(agen)
    removed = await purge_orphan_attachments(db)
    await db.commit()
    await agen.aclose()

    assert removed == 1
    assert not stranded.exists()
    assert fresh.exists()


async def test_scheduled_export_writes_user_zip(auth, tmp_path, monkeypatch):
    import zipfile

    from app.config import settings
    from app.database import get_db
    from app.main import app
    from app.services.scheduled_export import write_user_exports

    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    client, alice, _ = auth
    fid = await _default_folder_id(client, alice)
    await client.post(
        "/api/v1/notes",
        headers=alice,
        json={"folder_id": fid, "title": "Keep me", "body_text": "Keep me\nsafe"},
    )

    agen = app.dependency_overrides[get_db]()
    db = await anext(agen)
    exported = await write_user_exports(db)
    await db.commit()
    await agen.aclose()

    assert exported == 1
    zpath = tmp_path / "exports" / "alice.zip"
    assert zpath.exists()
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
        assert "notes.json" in names
        assert any(n.endswith(".md") and "Keep me" in n for n in names)


async def test_verify_password_endpoint(auth):
    client, alice, _ = auth
    assert (
        await client.post(
            "/api/v1/auth/verify-password", headers=alice, json={"password": "password123"}
        )
    ).status_code == 204
    assert (
        await client.post(
            "/api/v1/auth/verify-password", headers=alice, json={"password": "wrong"}
        )
    ).status_code == 401

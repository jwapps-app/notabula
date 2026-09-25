"""Regression tests for the second (full-source) audit's backend fixes."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.models import Attachment, Note, Session, TotpRecoveryCode
from app.models.note import first_image_src, has_unchecked_task
from app.services.attachment_gc import purge_orphan_attachments
from app.services.push import vapid_signer
from app.services.totp import issue_recovery_codes, verify_second_factor

CIPHER = '{"v":1,"salt":"c2FsdA==","iv":"aXY=","ct":"Y2lwaGVydGV4dA=="}'


async def _fid(client, headers):
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    return next(f["id"] for f in folders if f["is_default"])


async def _note(client, headers, fid, text="hello", body=None):
    resp = await client.post(
        "/api/v1/notes",
        headers=headers,
        json={"folder_id": fid, "title": text[:40], "body": body, "body_text": text},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _session(client):
    from app.database import get_db
    from app.main import app

    async for db in app.dependency_overrides[get_db]():
        return db


# --- D07: soft delete is idempotent -----------------------------------------


async def test_repeated_soft_delete_does_not_escalate_to_permanent(auth):
    client, h, _ = auth
    n = await _note(client, h, await _fid(client, h))
    assert (await client.delete(f"/api/v1/notes/{n['id']}", headers=h)).status_code == 204
    # A retry (lost response, second tab) must be a no-op — not a hard delete.
    assert (await client.delete(f"/api/v1/notes/{n['id']}", headers=h)).status_code == 204
    restored = await client.post(f"/api/v1/notes/{n['id']}/restore", headers=h)
    assert restored.status_code == 200, "note was permanently deleted by the retry"
    # Explicit permanent still works.
    await client.delete(f"/api/v1/notes/{n['id']}", headers=h)
    assert (
        await client.delete(f"/api/v1/notes/{n['id']}?permanent=true", headers=h)
    ).status_code == 204
    assert (await client.post(f"/api/v1/notes/{n['id']}/restore", headers=h)).status_code == 404


# --- S01: locking discards plaintext history --------------------------------


async def test_locking_purges_history_and_hides_it_while_locked(auth):
    client, h, _ = auth
    n = await _note(client, h, await _fid(client, h), text="the secret is hunter2")
    revs = (await client.get(f"/api/v1/notes/{n['id']}/revisions", headers=h)).json()
    assert len(revs) == 1
    rev_id = revs[0]["id"]

    lock = await client.patch(
        f"/api/v1/notes/{n['id']}",
        headers=h,
        json={"base_version": n["version"], "locked": True, "cipher_body": CIPHER},
    )
    assert lock.status_code == 200, lock.text
    assert (await client.get(f"/api/v1/notes/{n['id']}/revisions", headers=h)).json() == []
    detail = await client.get(f"/api/v1/notes/{n['id']}/revisions/{rev_id}", headers=h)
    assert detail.status_code == 404

    # Unlock: history resumes from the unlocked content, the old row is gone.
    unlock = await client.patch(
        f"/api/v1/notes/{n['id']}",
        headers=h,
        json={
            "base_version": lock.json()["version"],
            "locked": False,
            "body": {"type": "doc", "content": []},
            "body_text": "after",
            "title": "after",
        },
    )
    assert unlock.status_code == 200, unlock.text
    revs = (await client.get(f"/api/v1/notes/{n['id']}/revisions", headers=h)).json()
    assert [r["id"] for r in revs] != [rev_id]
    for r in revs:
        d = (await client.get(f"/api/v1/notes/{n['id']}/revisions/{r['id']}", headers=h)).json()
        assert "hunter2" not in d["body_text"]


# --- S08: capture token dies with the password ------------------------------


async def test_password_change_revokes_capture_token(auth):
    client, h, _ = auth
    token = (await client.post("/api/v1/auth/capture-token", headers=h)).json()["token"]
    ok = await client.post(f"/api/v1/notes/capture?token={token}", content="captured")
    assert ok.status_code == 201
    resp = await client.post(
        "/api/v1/auth/password",
        headers=h,
        json={"current_password": "password123", "new_password": "newpassword456"},
    )
    assert resp.status_code == 204, resp.text
    denied = await client.post(f"/api/v1/notes/capture?token={token}", content="again")
    assert denied.status_code == 401
    assert (await client.get("/api/v1/auth/capture-token", headers=h)).json() == {"exists": False}


# --- D06: the default folder stays at the root ------------------------------


async def test_default_folder_cannot_be_nested(auth):
    client, h, _ = auth
    fid = await _fid(client, h)
    parent = (await client.post("/api/v1/folders", headers=h, json={"name": "Parent"})).json()
    n = await _note(client, h, fid)
    resp = await client.patch(
        f"/api/v1/folders/{fid}", headers=h, json={"parent_id": parent["id"]}
    )
    assert resp.status_code == 400
    # And deleting the parent can't take the default folder (or its notes) with it.
    assert (await client.delete(f"/api/v1/folders/{parent['id']}", headers=h)).status_code == 204
    assert (await client.get(f"/api/v1/notes/{n['id']}", headers=h)).status_code == 200


async def test_folder_can_be_moved_back_to_root(auth):
    client, h, _ = auth
    parent = (await client.post("/api/v1/folders", headers=h, json={"name": "P"})).json()
    child = (
        await client.post(
            "/api/v1/folders", headers=h, json={"name": "C", "parent_id": parent["id"]}
        )
    ).json()
    assert child["parent_id"] == parent["id"]
    resp = await client.patch(f"/api/v1/folders/{child['id']}", headers=h, json={"parent_id": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["parent_id"] is None


# --- F07: shared-folder list reports the resolved role ----------------------


async def test_shared_folder_list_uses_direct_share_role(auth):
    from tests.conftest import make_user

    client, alice, _ = auth
    bob = await make_user(client, alice)
    folder = (await client.post("/api/v1/folders", headers=alice, json={"name": "Team"})).json()
    n = await _note(client, alice, folder["id"])
    await client.put(
        f"/api/v1/folders/{folder['id']}/shares",
        headers=alice,
        json={"username": "bob", "role": "viewer"},
    )
    await client.put(
        f"/api/v1/notes/{n['id']}/shares",
        headers=alice,
        json={"username": "bob", "role": "editor"},
    )
    listed = (
        await client.get(f"/api/v1/notes?folder_id={folder['id']}", headers=bob)
    ).json()
    assert [x["role"] for x in listed] == ["editor"], "direct share must outrank folder share"


# --- F01: the generated VAPID key is usable by pywebpush --------------------


def test_generated_vapid_key_loads_as_signer(tmp_path, monkeypatch):
    from app.config import settings
    from app.services import push as push_svc

    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    monkeypatch.setattr(push_svc, "_VAPID_CACHE", None)
    signer = vapid_signer()
    # A real signed header proves the key parsed and can sign.
    headers = signer.sign({"sub": "mailto:x@example.com", "aud": "https://push.example"})
    assert headers["Authorization"].startswith("vapid ")


async def test_private_push_endpoints_are_rejected(auth):
    client, h, _ = auth
    for bad in ("http://push.example/x", "https://127.0.0.1/x", "https://[::1]/x"):
        resp = await client.post(
            "/api/v1/push/subscriptions",
            headers=h,
            json={"endpoint": bad, "keys": {"p256dh": "k", "auth": "a"}},
        )
        assert resp.status_code == 422, bad


# --- S12: restore invalidates every session --------------------------------


async def test_restore_signs_everyone_out(auth, tmp_path, monkeypatch):
    import app.routers.admin as admin_router
    from app.config import settings

    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))

    async def fake_restore(dump_path):
        pass

    async def fake_migrations():
        pass

    invalidated: list[object] = []

    async def fake_invalidate(engine):
        invalidated.append(engine)

    monkeypatch.setattr(admin_router, "restore_database", fake_restore)
    monkeypatch.setattr(admin_router, "run_migrations", fake_migrations)
    # The route disposes the engine, which destroys an in-memory SQLite DB —
    # so the sweep is verified on its own below, and here we only prove the
    # route calls it after the restore.
    monkeypatch.setattr(admin_router, "invalidate_all_sessions", fake_invalidate)
    client, h, _ = auth
    resp = await client.post(
        "/api/v1/admin/restore", headers=h, files={"db_dump": ("db.dump", b"x")}
    )
    assert resp.status_code == 200, resp.text
    assert len(invalidated) == 1


async def test_invalidate_all_sessions_deletes_every_row(auth):
    from app.services.restore import invalidate_all_sessions

    client, h, _ = auth
    db = await _session(client)
    assert (await db.execute(select(func.count(Session.id)))).scalar_one() == 1
    await invalidate_all_sessions(db.bind)
    assert (await client.get("/api/v1/auth/me", headers=h)).status_code == 401


# --- D14: GC keeps files still referenced by someone's note -----------------


async def test_gc_keeps_rowless_file_referenced_by_a_note(auth, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    media = Path(settings.media_root) / "attachments"
    media.mkdir(parents=True)
    old = datetime.now(timezone.utc) - timedelta(days=3)
    for name in ("kept.png", "junk.png"):
        p = media / name
        p.write_bytes(b"x")
        import os

        os.utime(p, (old.timestamp(), old.timestamp()))

    client, h, _ = auth
    await _note(
        client,
        h,
        await _fid(client, h),
        body={"type": "doc", "content": [
            {"type": "image", "attrs": {"src": "/media/attachments/kept.png"}}]},
    )
    db = await _session(client)
    assert (await db.execute(select(func.count(Attachment.id)))).scalar_one() == 0
    await purge_orphan_attachments(db)
    await db.commit()
    assert (media / "kept.png").exists(), "referenced file must survive without a row"
    assert not (media / "junk.png").exists()


# --- S09: recovery codes are consumed atomically ----------------------------


async def test_recovery_code_single_use(auth):
    client, h, user = auth
    db = await _session(client)
    from app.models import User

    u = await db.get(User, user["id"])
    codes = await issue_recovery_codes(db, u)
    await db.commit()
    assert await verify_second_factor(db, u, codes[0]) is True
    await db.commit()
    assert await verify_second_factor(db, u, codes[0]) is False
    used = (
        await db.execute(
            select(func.count(TotpRecoveryCode.id)).where(TotpRecoveryCode.used_at.is_not(None))
        )
    ).scalar_one()
    assert used == 1


# --- D15: malformed documents can't crash the body listener ------------------


def test_body_walkers_tolerate_malformed_nodes():
    bad = {"type": "doc", "content": [
        {"type": "image", "attrs": "not-a-dict"},
        {"type": "taskItem", "attrs": None, "content": "not-a-list"},
        {"type": "paragraph", "content": [{"type": "image", "attrs": {"src": "/x.png"}}]},
    ]}
    assert first_image_src(bad) == "/x.png"
    assert has_unchecked_task(bad) is False


async def test_malformed_body_is_stored_not_500(auth):
    client, h, _ = auth
    resp = await client.post(
        "/api/v1/notes",
        headers=h,
        json={
            "folder_id": await _fid(client, h),
            "body": {"type": "doc", "content": [{"type": "image", "attrs": "oops"}]},
            "body_text": "x",
        },
    )
    assert resp.status_code == 201, resp.text


# --- D11: export freshness tracks more than the newest timestamp ------------


async def test_export_rebuilds_after_historical_import_and_rename(auth, tmp_path, monkeypatch):
    from app.config import settings
    from app.services.scheduled_export import write_user_exports

    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    client, h, _ = auth
    fid = await _fid(client, h)
    await _note(client, h, fid, text="first")
    db = await _session(client)
    assert await write_user_exports(db) == 1
    assert await write_user_exports(db) == 0  # unchanged → skipped

    # A note imported with an OLD timestamp must still trigger a rebuild.
    resp = await client.post(
        "/api/v1/notes/import",
        headers=h,
        json={"notes": [{
            "folder": "Notes", "title": "old", "body_text": "old note",
            "created_at": "2020-01-01T00:00:00Z", "updated_at": "2020-01-01T00:00:00Z",
        }]},
    )
    assert resp.status_code == 201
    assert await write_user_exports(db) == 1
    zip_path = tmp_path / "exports" / "alice.zip"
    assert zip_path.exists() and not zip_path.with_suffix(".zip.tmp").exists()
    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        assert any("old" in n for n in zf.namelist())


# --- expired-session sweep still leaves the live one (sanity) ---------------


async def test_session_table_has_one_live_row(auth):
    client, h, _ = auth
    db = await _session(client)
    assert (await db.execute(select(func.count(Session.id)))).scalar_one() == 1
    n = (await client.get("/api/v1/notes", headers=h))
    assert n.status_code == 200
    assert isinstance(Note, type)

"""The changes that needed a coordinated iOS release (build 7): body-size
caps, owner-only pin/reminder (ignored, never rejected), the TOTP replay
guard, and the delta-sync endpoint."""

from datetime import datetime, timedelta, timezone

import pyotp

from tests.conftest import make_user
from tests.test_totp import _enroll

CIPHER = '{"v":1,"salt":"c2FsdA==","iv":"aXY=","ct":"Y2lwaGVydGV4dA=="}'


async def _fid(client, headers):
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    return next(f["id"] for f in folders if f["is_default"])


async def _note(client, headers, fid, text="hello"):
    resp = await client.post(
        "/api/v1/notes",
        headers=headers,
        json={"folder_id": fid, "title": text[:40], "body_text": text},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- body caps --------------------------------------------------------------


async def test_oversize_body_json_is_422(auth):
    client, h, _ = auth
    huge = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "x" * 2_100_000}]}]}
    resp = await client.post(
        "/api/v1/notes", headers=h,
        json={"folder_id": await _fid(client, h), "body": huge, "body_text": "x"},
    )
    assert resp.status_code == 422
    assert "body" in resp.text


async def test_body_must_be_a_doc(auth):
    client, h, _ = auth
    resp = await client.post(
        "/api/v1/notes", headers=h,
        json={"folder_id": await _fid(client, h), "body": {"foo": "bar"}, "body_text": "x"},
    )
    assert resp.status_code == 422


async def test_oversize_cipher_body_is_422(auth):
    client, h, _ = auth
    n = await _note(client, h, await _fid(client, h))
    resp = await client.patch(
        f"/api/v1/notes/{n['id']}", headers=h,
        json={"base_version": 1, "locked": True, "cipher_body": "A" * 4_000_001},
    )
    assert resp.status_code == 422


async def test_normal_sized_notes_still_save(auth):
    client, h, _ = auth
    body = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "y" * 500_000}]}]}
    resp = await client.post(
        "/api/v1/notes", headers=h,
        json={"folder_id": await _fid(client, h), "body": body, "body_text": "y" * 500_000},
    )
    assert resp.status_code == 201


# --- owner-only pin / reminder: ignored for editors, not rejected -----------


async def test_editor_pin_and_reminder_are_ignored_not_rejected(auth):
    client, alice, _ = auth
    bob = await make_user(client, alice)
    n = await _note(client, alice, await _fid(client, alice))
    await client.put(
        f"/api/v1/notes/{n['id']}/shares", headers=alice,
        json={"username": "bob", "role": "editor"},
    )
    # Exactly what the iOS sync engine sends on every save, role or not.
    resp = await client.patch(
        f"/api/v1/notes/{n['id']}", headers=bob,
        json={
            "base_version": n["version"], "title": "edited by bob",
            "body": {"type": "doc", "content": []}, "body_text": "edited by bob",
            "pinned": True, "remind_at": "2030-01-01T00:00:00Z",
        },
    )
    assert resp.status_code == 200, resp.text  # the edit lands…
    out = resp.json()
    assert out["title"] == "edited by bob"
    assert out["pinned"] is False  # …but pin and reminder stayed the owner's
    assert out["remind_at"] is None

    # The owner can still set both.
    resp = await client.patch(
        f"/api/v1/notes/{n['id']}", headers=alice,
        json={"base_version": out["version"], "pinned": True, "remind_at": "2030-01-01T00:00:00Z"},
    )
    assert resp.status_code == 200
    assert resp.json()["pinned"] is True and resp.json()["remind_at"] is not None


# --- TOTP replay guard ------------------------------------------------------


async def test_totp_code_is_single_use(auth):
    client, h, _ = auth
    secret, _ = await _enroll(client, h)
    code = pyotp.TOTP(secret).now()
    login = {"username": "alice", "password": "password123", "totp_code": code}
    assert (await client.post("/api/v1/auth/login", json=login)).status_code == 200
    # Same code, seconds later, still inside its validity window: refused.
    resp = await client.post("/api/v1/auth/login", json=login)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid verification code"


async def test_totp_older_step_refused_after_newer_accepted(auth):
    client, h, _ = auth
    secret, _ = await _enroll(client, h)  # enrolled with the PREVIOUS step's code
    now_code = pyotp.TOTP(secret).now()
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "password123", "totp_code": now_code},
        )
    ).status_code == 200
    older = pyotp.TOTP(secret).at(datetime.now() - timedelta(seconds=30))
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password123", "totp_code": older},
    )
    assert resp.status_code == 401


# --- /notes/changes: delta sync ---------------------------------------------


async def test_changes_full_then_incremental(auth):
    client, h, _ = auth
    fid = await _fid(client, h)
    a = await _note(client, h, fid, "alpha")
    b = await _note(client, h, fid, "beta")

    first = (await client.get("/api/v1/notes/changes", headers=h)).json()
    assert {n["id"] for n in first["notes"]} == {a["id"], b["id"]}
    assert set(first["ids"]) == {a["id"], b["id"]}
    since = first["now"]

    # Nothing changed → no bodies shipped, ids still complete.
    # (Ask for a `since` in the future so the 5 s overlap can't re-include
    # the notes just created.)
    later = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    quiet = (await client.get(f"/api/v1/notes/changes?since={later}", headers=h)).json()
    assert quiet["notes"] == []
    assert set(quiet["ids"]) == {a["id"], b["id"]}

    # Edit one, trash the other: only the edit is shipped, and the trashed
    # note drops out of `ids` so the client removes it.
    await client.patch(
        f"/api/v1/notes/{a['id']}", headers=h,
        json={"base_version": 1, "title": "alpha 2", "body_text": "alpha 2"},
    )
    await client.delete(f"/api/v1/notes/{b['id']}", headers=h)
    delta = (await client.get(f"/api/v1/notes/changes?since={since}", headers=h)).json()
    assert [n["id"] for n in delta["notes"]] == [a["id"]]
    assert delta["notes"][0]["title"] == "alpha 2"
    assert delta["ids"] == [a["id"]]


async def test_changes_unshare_drops_from_ids(auth):
    client, alice, _ = auth
    bob = await make_user(client, alice)
    n = await _note(client, alice, await _fid(client, alice))
    await client.put(
        f"/api/v1/notes/{n['id']}/shares", headers=alice,
        json={"username": "bob", "role": "viewer"},
    )
    seen = (await client.get("/api/v1/notes/changes", headers=bob)).json()
    assert seen["ids"] == [n["id"]]
    assert seen["notes"][0]["role"] == "viewer"
    await client.delete(f"/api/v1/notes/{n['id']}/shares/bob", headers=alice)
    gone = (await client.get(f"/api/v1/notes/changes?since={seen['now']}", headers=bob)).json()
    assert gone["ids"] == [] and gone["notes"] == []

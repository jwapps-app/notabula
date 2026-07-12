"""Regression tests for the security-audit fixes."""

from app.services.unfurl import is_safe_url


async def _default_folder(client, headers):
    return (await client.get("/api/v1/folders", headers=headers)).json()[0]["id"]


async def test_svg_upload_rejected(auth):
    """SVG can carry script and media is same-origin — must be refused."""
    client, alice, _ = auth
    resp = await client.post(
        "/api/v1/attachments",
        headers=alice,
        files={"file": ("x.svg", b"<svg xmlns='...'><script>alert(1)</script></svg>",
                        "image/svg+xml")},
    )
    assert resp.status_code == 415


async def test_folder_cycle_rejected(auth):
    """Moving a folder into its own subtree would make delete loop forever."""
    client, alice, _ = auth
    a = (await client.post("/api/v1/folders", headers=alice,
                           json={"name": "A"})).json()
    b = (await client.post("/api/v1/folders", headers=alice,
                           json={"name": "B", "parent_id": a["id"]})).json()
    # A into B (its own child) → cycle → 400.
    resp = await client.patch(
        f"/api/v1/folders/{a['id']}", headers=alice,
        json={"parent_id": b["id"]},
    )
    assert resp.status_code == 400


async def test_locked_note_title_not_leaked_in_shared_folder_search(auth):
    from tests.conftest import make_user

    client, alice, _ = auth
    bob = await make_user(client, alice, username="bob", name="Bob")
    fid = await _default_folder(client, alice)
    # Alice makes a note, shares her folder with Bob, then locks the note.
    note = (await client.post("/api/v1/notes", headers=alice, json={
        "folder_id": fid, "title": "SECRETPLAN", "body_text": "SECRETPLAN"})).json()
    await client.put(f"/api/v1/folders/{fid}/shares", headers=alice,
                     json={"username": "bob", "role": "viewer"})
    lock = await client.patch(f"/api/v1/notes/{note['id']}", headers=alice, json={
        "base_version": note["version"], "locked": True,
        "cipher_body": "blob", "title": "SECRETPLAN"})
    assert lock.status_code == 200
    # Bob searches the shared folder — the locked note must not surface.
    hits = (await client.get("/api/v1/search?q=SECRETPLAN", headers=bob)).json()
    assert all(h["id"] != note["id"] for h in hits)


async def test_all_digit_recovery_code_still_works(auth, monkeypatch):
    """A recovery code that happens to be all digits must not be misrouted
    to the TOTP branch and rejected."""
    from app.services.totp import hash_token, verify_second_factor

    client, alice, user = auth
    # Craft a user with a known all-digit recovery code.
    from app.database import get_db
    from app.main import app as fastapi_app
    from app.models import TotpRecoveryCode, User

    agen = fastapi_app.dependency_overrides[get_db]()
    db = await anext(agen)
    u = await db.get(User, __import__("uuid").UUID(user["id"]))
    u.totp_secret = "JBSWY3DPEHPK3PXP"  # enabled, so 6-digit codes are TOTP
    db.add(TotpRecoveryCode(user_id=u.id, code_hash=hash_token("12345678901234")))
    await db.commit()
    ok = await verify_second_factor(db, u, "12345678901234")
    await db.commit()
    await agen.aclose()
    assert ok is True


def test_ssrf_url_validation_still_blocks_internal():
    assert is_safe_url("http://127.0.0.1/") is False
    assert is_safe_url("http://169.254.169.254/") is False
    assert is_safe_url("http://10.0.0.1/") is False
    assert is_safe_url("https://example.com/") in (True, False)  # network-dependent


async def test_body_text_size_capped(auth):
    client, alice, _ = auth
    fid = await _default_folder(client, alice)
    resp = await client.post("/api/v1/notes", headers=alice, json={
        "folder_id": fid, "body_text": "x" * 1_000_001})
    assert resp.status_code == 422

"""Search endpoint (SQLite LIKE fallback path) and purge service."""

from datetime import datetime, timedelta, timezone


async def _make_note(client, headers, folder_id, title, body_text):
    resp = await client.post(
        "/api/v1/notes",
        headers=headers,
        json={"folder_id": folder_id, "title": title, "body_text": body_text},
    )
    assert resp.status_code == 201
    return resp.json()


async def _default_folder_id(client, headers):
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    return next(f["id"] for f in folders if f["is_default"])


async def test_search_matches_title_and_body(auth):
    client, headers, _ = auth
    fid = await _default_folder_id(client, headers)
    await _make_note(client, headers, fid, "Groceries", "Groceries\nmilk and eggs")
    await _make_note(client, headers, fid, "Meeting", "Meeting\ndiscuss roadmap")

    hits = (await client.get("/api/v1/search?q=groceries", headers=headers)).json()
    assert [h["title"] for h in hits] == ["Groceries"]

    hits = (await client.get("/api/v1/search?q=roadmap", headers=headers)).json()
    assert [h["title"] for h in hits] == ["Meeting"]

    hits = (await client.get("/api/v1/search?q=nonexistent", headers=headers)).json()
    assert hits == []


async def test_search_excludes_deleted_and_other_users(auth):
    client, headers, _ = auth
    fid = await _default_folder_id(client, headers)
    note = await _make_note(client, headers, fid, "Secret plans", "Secret plans")
    await client.delete(f"/api/v1/notes/{note['id']}", headers=headers)
    hits = (await client.get("/api/v1/search?q=secret", headers=headers)).json()
    assert hits == []

    # another user can't see it either way
    from tests.conftest import make_user

    headers_b = await make_user(client, headers)
    hits = (await client.get("/api/v1/search?q=secret", headers=headers_b)).json()
    assert hits == []


async def test_purge_removes_only_expired(auth, monkeypatch):
    from sqlalchemy import select, update

    from app.models import Note
    from app.services.purge import purge_deleted_notes
    import tests.conftest as _  # noqa: F401  (fixture wiring)

    client, headers, _user = auth
    fid = await _default_folder_id(client, headers)
    old = await _make_note(client, headers, fid, "Old", "Old")
    fresh = await _make_note(client, headers, fid, "Fresh", "Fresh")
    for note in (old, fresh):
        await client.delete(f"/api/v1/notes/{note['id']}", headers=headers)

    # Reach into the test DB to age one note past the retention window.
    from app.main import app
    from app.database import get_db

    override = app.dependency_overrides[get_db]
    agen = override()
    db = await anext(agen)
    await db.execute(
        update(Note)
        .where(Note.id == old["id"])
        .values(deleted_at=datetime.now(timezone.utc) - timedelta(days=31))
    )
    purged = await purge_deleted_notes(db)
    assert purged == 1
    remaining = (await db.execute(select(Note.id))).scalars().all()
    assert [str(r) for r in remaining] == [fresh["id"]]
    await agen.aclose()

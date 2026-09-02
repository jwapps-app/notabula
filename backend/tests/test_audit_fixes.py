"""Regression tests for the 2026-09 server audit fixes."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import Note, Session, Tag, note_tags
from app.models.note import has_unchecked_task
from app.services.purge import purge_expired_sessions
from app.services.tags import sync_note_tags
from app.services.unfurl import MAX_BYTES, _read_capped


async def _default_folder_id(client, headers):
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    return next(f["id"] for f in folders if f["is_default"])


# --- S12: expired sessions are swept --------------------------------------


async def test_expired_sessions_are_purged(auth):
    client, headers, user = auth
    from app.database import get_db
    from app.main import app

    override = app.dependency_overrides[get_db]
    async for db in override():
        live = (await db.execute(select(func.count(Session.id)))).scalar_one()
        assert live == 1
        db.add(
            Session(
                user_id=user["id"],
                token_hash="x" * 64,
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
        )
        await db.flush()
        removed = await purge_expired_sessions(db)
        assert removed == 1
        assert (await db.execute(select(func.count(Session.id)))).scalar_one() == 1
        # The caller's live session survived — it still authenticates.
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200


# --- P2: tag sync is a no-op when hashtags didn't change --------------------


async def test_tag_sync_noop_when_unchanged_and_sweeps_on_removal(auth):
    client, headers, user = auth
    fid = await _default_folder_id(client, headers)
    resp = await client.post(
        "/api/v1/notes",
        headers=headers,
        json={"folder_id": fid, "title": "t", "body_text": "hello #alpha #beta"},
    )
    assert resp.status_code == 201
    note_id = resp.json()["id"]

    from app.database import get_db
    from app.main import app

    override = app.dependency_overrides[get_db]
    async for db in override():
        note = await db.get(Note, note_id)
        before = set(
            (
                await db.execute(select(note_tags.c.tag_id).where(note_tags.c.note_id == note.id))
            ).scalars()
        )
        assert len(before) == 2

        # Same hashtags, different prose → links must be left exactly as-is
        # (not deleted and re-inserted, which would churn on every autosave).
        note.body_text = "hello again #beta #alpha"
        await sync_note_tags(db, note, user["id"])
        after = set(
            (
                await db.execute(select(note_tags.c.tag_id).where(note_tags.c.note_id == note.id))
            ).scalars()
        )
        assert after == before

        # Removing a tag still orphans-and-sweeps it.
        note.body_text = "only #alpha now"
        await sync_note_tags(db, note, user["id"])
        names = set((await db.execute(select(Tag.name).where(Tag.owner_id == user["id"]))).scalars())
        assert names == {"alpha"}
        await db.commit()


# --- S5: unfurl never buffers more than MAX_BYTES ---------------------------


class _FakeStream:
    """Stands in for httpx.Response: yields chunks forever unless stopped."""

    def __init__(self, chunk: bytes):
        self.chunk = chunk
        self.yielded = 0

    async def aiter_bytes(self):
        while True:
            self.yielded += len(self.chunk)
            yield self.chunk


async def test_read_capped_stops_at_max_bytes():
    fake = _FakeStream(b"x" * 64 * 1024)
    data = await _read_capped(fake)  # type: ignore[arg-type]
    assert len(data) == MAX_BYTES
    # It stopped pulling once the cap was reached — not "read everything,
    # then slice" (which is the memory-DoS the fix removes).
    assert fake.yielded <= MAX_BYTES + len(fake.chunk)


# --- P4: has_open_tasks tracks the body -------------------------------------


@pytest.mark.parametrize(
    "body,expected",
    [
        (None, False),
        ({"type": "doc", "content": []}, False),
        (
            {"type": "doc", "content": [{"type": "taskList", "content": [
                {"type": "taskItem", "attrs": {"checked": True}}]}]},
            False,
        ),
        (
            {"type": "doc", "content": [{"type": "taskList", "content": [
                {"type": "taskItem", "attrs": {"checked": True}},
                {"type": "taskItem", "attrs": {"checked": False}}]}]},
            True,
        ),
    ],
)
def test_has_unchecked_task(body, expected):
    assert has_unchecked_task(body) is expected


async def test_has_open_tasks_column_follows_body_edits(auth):
    client, headers, _ = auth
    fid = await _default_folder_id(client, headers)
    todo = {"type": "doc", "content": [{"type": "taskList", "content": [
        {"type": "taskItem", "attrs": {"checked": False},
         "content": [{"type": "paragraph"}]}]}]}
    resp = await client.post(
        "/api/v1/notes", headers=headers,
        json={"folder_id": fid, "title": "todo", "body": todo, "body_text": "todo"},
    )
    nid = resp.json()["id"]
    assert [n["id"] for n in (await client.get("/api/v1/notes?view=tasks", headers=headers)).json()] == [nid]

    done = {"type": "doc", "content": [{"type": "taskList", "content": [
        {"type": "taskItem", "attrs": {"checked": True},
         "content": [{"type": "paragraph"}]}]}]}
    resp = await client.patch(
        f"/api/v1/notes/{nid}", headers=headers,
        json={"base_version": 1, "body": done, "body_text": "todo"},
    )
    assert resp.status_code == 200, resp.text
    assert (await client.get("/api/v1/notes?view=tasks", headers=headers)).json() == []


# --- S3: unknown usernames fail the same way as bad passwords ---------------


async def test_login_unknown_user_is_plain_401(auth):
    client, _, _ = auth
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "nobody", "password": "whatever"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Incorrect username or password"

"""Edit history: session coalescing, attribution, diffing data, access."""

from datetime import datetime, timedelta, timezone

from tests.conftest import make_user


async def _default_folder_id(client, headers):
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    return next(f["id"] for f in folders if f["is_default"])


async def _make_note(client, headers, folder_id, title="Plan", text="Plan\nstep one"):
    resp = await client.post(
        "/api/v1/notes",
        headers=headers,
        json={"folder_id": folder_id, "title": title, "body_text": text},
    )
    assert resp.status_code == 201
    return resp.json()


async def _age_latest_revision(note_id):
    """Push the newest revision out of the coalescing window."""
    from sqlalchemy import select, update

    from app.database import get_db
    from app.main import app
    from app.models import NoteRevision

    agen = app.dependency_overrides[get_db]()
    db = await anext(agen)
    latest = (
        await db.execute(
            select(NoteRevision)
            .where(NoteRevision.note_id == note_id)
            .order_by(NoteRevision.created_at.desc())
            .limit(1)
        )
    ).scalar_one()
    old = datetime.now(timezone.utc) - timedelta(minutes=30)
    await db.execute(
        update(NoteRevision)
        .where(NoteRevision.id == latest.id)
        .values(created_at=old, updated_at=old)
    )
    await db.commit()
    await agen.aclose()


async def test_saves_coalesce_into_one_session(auth):
    client, alice, _ = auth
    fid = await _default_folder_id(client, alice)
    note = await _make_note(client, alice, fid)

    # three rapid autosaves — same session
    for i, text in enumerate(["Plan\nstep one, two", "Plan\nsteps", "Plan\nfinal"], start=1):
        await client.patch(
            f"/api/v1/notes/{note['id']}",
            headers=alice,
            json={"base_version": i, "body_text": text},
        )
    revs = (
        await client.get(f"/api/v1/notes/{note['id']}/revisions", headers=alice)
    ).json()
    assert len(revs) == 1
    assert revs[0]["editor_name"] == "Alice"
    assert revs[0]["version"] == 4


async def test_sessions_split_by_time_and_editor(auth):
    client, alice, _ = auth
    bob = await make_user(client, alice)
    fid = await _default_folder_id(client, alice)
    note = await _make_note(client, alice, fid)
    await client.put(
        f"/api/v1/notes/{note['id']}/shares",
        headers=alice,
        json={"username": "bob", "role": "editor"},
    )

    # bob edits → different editor, new session even inside the window
    await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=bob,
        json={"base_version": 1, "body_text": "Plan\nbob was here"},
    )
    await _age_latest_revision(note["id"])
    # alice edits much later → third session
    await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=alice,
        json={"base_version": 2, "body_text": "Plan\nalice again"},
    )

    revs = (
        await client.get(f"/api/v1/notes/{note['id']}/revisions", headers=alice)
    ).json()
    assert [r["editor_name"] for r in revs] == ["Alice", "Bob", "Alice"]


async def test_revision_detail_carries_prev_text_for_redline(auth):
    client, alice, _ = auth
    fid = await _default_folder_id(client, alice)
    note = await _make_note(client, alice, fid, text="Plan\nstep one")
    await _age_latest_revision(note["id"])
    await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=alice,
        json={"base_version": 1, "body_text": "Plan\nstep one and two"},
    )

    revs = (
        await client.get(f"/api/v1/notes/{note['id']}/revisions", headers=alice)
    ).json()
    assert len(revs) == 2
    detail = (
        await client.get(
            f"/api/v1/notes/{note['id']}/revisions/{revs[0]['id']}", headers=alice
        )
    ).json()
    assert detail["body_text"] == "Plan\nstep one and two"
    assert detail["prev_body_text"] == "Plan\nstep one"
    # the oldest revision diffs against nothing
    first = (
        await client.get(
            f"/api/v1/notes/{note['id']}/revisions/{revs[1]['id']}", headers=alice
        )
    ).json()
    assert first["prev_body_text"] == ""


async def test_pin_toggle_creates_no_revision(auth):
    client, alice, _ = auth
    fid = await _default_folder_id(client, alice)
    note = await _make_note(client, alice, fid)
    await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=alice,
        json={"base_version": 1, "pinned": True},
    )
    revs = (
        await client.get(f"/api/v1/notes/{note['id']}/revisions", headers=alice)
    ).json()
    assert len(revs) == 1  # just the creation snapshot


async def test_history_access_follows_note_access(auth):
    client, alice, _ = auth
    bob = await make_user(client, alice)
    fid = await _default_folder_id(client, alice)
    note = await _make_note(client, alice, fid)

    url = f"/api/v1/notes/{note['id']}/revisions"
    assert (await client.get(url, headers=bob)).status_code == 404  # no access
    await client.put(
        f"/api/v1/notes/{note['id']}/shares",
        headers=alice,
        json={"username": "bob", "role": "viewer"},
    )
    assert (await client.get(url, headers=bob)).status_code == 200  # viewer may read


async def test_user_list_for_share_picker(auth):
    client, alice, _ = auth
    await make_user(client, alice)
    users = (await client.get("/api/v1/auth/users", headers=alice)).json()
    assert users == [{"username": "bob", "name": "Bob"}]  # excludes me

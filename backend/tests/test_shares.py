"""Sharing: roles, access enforcement, folder shares, unshare."""

from tests.conftest import make_user


async def _default_folder_id(client, headers):
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    return next(f["id"] for f in folders if f["is_default"])


async def _make_note(client, headers, folder_id, title="Groceries", text="Groceries\nmilk"):
    resp = await client.post(
        "/api/v1/notes",
        headers=headers,
        json={"folder_id": folder_id, "title": title, "body_text": text},
    )
    assert resp.status_code == 201
    return resp.json()


async def _setup(auth):
    client, alice, _ = auth
    bob = await make_user(client, alice)
    fid = await _default_folder_id(client, alice)
    note = await _make_note(client, alice, fid)
    return client, alice, bob, fid, note


async def test_viewer_can_read_not_write(auth):
    client, alice, bob, fid, note = await _setup(auth)

    resp = await client.put(
        f"/api/v1/notes/{note['id']}/shares",
        headers=alice,
        json={"username": "bob", "role": "viewer"},
    )
    assert resp.status_code == 200
    assert resp.json() == [{"username": "bob", "name": "Bob", "role": "viewer"}]

    # bob sees it in his shared list, annotated
    shared = (await client.get("/api/v1/notes?shared=true", headers=bob)).json()
    assert [(s["title"], s["role"], s["owner_name"]) for s in shared] == [
        ("Groceries", "viewer", "Alice")
    ]

    # read yes, write/delete no
    got = await client.get(f"/api/v1/notes/{note['id']}", headers=bob)
    assert got.status_code == 200
    assert got.json()["role"] == "viewer"
    assert (
        await client.patch(
            f"/api/v1/notes/{note['id']}",
            headers=bob,
            json={"base_version": 1, "title": "hax"},
        )
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/notes/{note['id']}", headers=bob)
    ).status_code == 403


async def test_editor_edits_content_only(auth):
    client, alice, bob, fid, note = await _setup(auth)
    await client.put(
        f"/api/v1/notes/{note['id']}/shares",
        headers=alice,
        json={"username": "bob", "role": "editor"},
    )

    resp = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=bob,
        json={"base_version": 1, "title": "Groceries+", "body_text": "Groceries+\nmilk, eggs"},
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 2

    # but cannot move, delete, or manage shares
    other = (
        await client.post("/api/v1/folders", headers=bob, json={"name": "Mine"})
    ).json()
    assert (
        await client.patch(
            f"/api/v1/notes/{note['id']}",
            headers=bob,
            json={"base_version": 2, "folder_id": other["id"]},
        )
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/notes/{note['id']}", headers=bob)
    ).status_code == 403
    assert (
        await client.put(
            f"/api/v1/notes/{note['id']}/shares",
            headers=bob,
            json={"username": "alice", "role": "editor"},
        )
    ).status_code == 404


async def test_folder_share_covers_current_and_future_notes(auth):
    client, alice, bob, fid, note = await _setup(auth)
    await client.put(
        f"/api/v1/folders/{fid}/shares",
        headers=alice,
        json={"username": "bob", "role": "viewer"},
    )

    # bob's sidebar sees the folder
    folders = (await client.get("/api/v1/shared/folders", headers=bob)).json()
    assert [(f["name"], f["owner_name"], f["role"]) for f in folders] == [
        ("Notes", "Alice", "viewer")
    ]

    # existing note visible via the shared folder view
    listing = (
        await client.get(f"/api/v1/notes?folder_id={fid}", headers=bob)
    ).json()
    assert [n["title"] for n in listing] == ["Groceries"]

    # a note added later is covered too
    await _make_note(client, alice, fid, title="Later", text="Later")
    listing = (
        await client.get(f"/api/v1/notes?folder_id={fid}", headers=bob)
    ).json()
    assert sorted(n["title"] for n in listing) == ["Groceries", "Later"]

    # direct note share upgrades bob on that one note
    await client.put(
        f"/api/v1/notes/{note['id']}/shares",
        headers=alice,
        json={"username": "bob", "role": "editor"},
    )
    got = (await client.get(f"/api/v1/notes/{note['id']}", headers=bob)).json()
    assert got["role"] == "editor"


async def test_unshare_revokes_access(auth):
    client, alice, bob, fid, note = await _setup(auth)
    await client.put(
        f"/api/v1/notes/{note['id']}/shares",
        headers=alice,
        json={"username": "bob", "role": "viewer"},
    )
    assert (
        await client.get(f"/api/v1/notes/{note['id']}", headers=bob)
    ).status_code == 200

    resp = await client.delete(
        f"/api/v1/notes/{note['id']}/shares/bob", headers=alice
    )
    assert resp.status_code == 200
    assert resp.json() == []
    assert (
        await client.get(f"/api/v1/notes/{note['id']}", headers=bob)
    ).status_code == 404
    assert (await client.get("/api/v1/notes?shared=true", headers=bob)).json() == []


async def test_share_validation(auth):
    client, alice, bob, fid, note = await _setup(auth)
    # nonexistent user
    assert (
        await client.put(
            f"/api/v1/notes/{note['id']}/shares",
            headers=alice,
            json={"username": "ghost", "role": "viewer"},
        )
    ).status_code == 404
    # sharing with yourself
    assert (
        await client.put(
            f"/api/v1/notes/{note['id']}/shares",
            headers=alice,
            json={"username": "alice", "role": "viewer"},
        )
    ).status_code == 400
    # non-owner cannot inspect shares
    assert (
        await client.get(f"/api/v1/notes/{note['id']}/shares", headers=bob)
    ).status_code == 404


async def test_deleted_notes_hidden_from_shared_users(auth):
    client, alice, bob, fid, note = await _setup(auth)
    await client.put(
        f"/api/v1/notes/{note['id']}/shares",
        headers=alice,
        json={"username": "bob", "role": "editor"},
    )
    await client.delete(f"/api/v1/notes/{note['id']}", headers=alice)  # soft delete
    assert (
        await client.get(f"/api/v1/notes/{note['id']}", headers=bob)
    ).status_code == 404
    assert (await client.get("/api/v1/notes?shared=true", headers=bob)).json() == []

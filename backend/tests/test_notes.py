"""Notes + folders: CRUD, soft delete, version conflicts, ownership isolation."""

BODY = {
    "type": "doc",
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Groceries"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "milk, eggs"}]},
    ],
}


async def _default_folder(client, headers):
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    return next(f for f in folders if f["is_default"])


async def test_default_folder_created(auth):
    client, headers, _ = auth
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    assert any(f["name"] == "Notes" and f["is_default"] for f in folders)


async def test_note_lifecycle(auth):
    client, headers, _ = auth
    folder = await _default_folder(client, headers)

    # create
    resp = await client.post(
        "/api/v1/notes",
        headers=headers,
        json={
            "folder_id": folder["id"],
            "title": "Groceries",
            "body": BODY,
            "body_text": "Groceries\nmilk, eggs",
        },
    )
    assert resp.status_code == 201, resp.text
    note = resp.json()
    assert note["version"] == 1

    # list shows preview without body
    items = (
        await client.get(
            f"/api/v1/notes?folder_id={folder['id']}", headers=headers
        )
    ).json()
    assert len(items) == 1
    assert items[0]["title"] == "Groceries"
    assert "milk" in items[0]["preview"]

    # update bumps version
    resp = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=headers,
        json={"base_version": 1, "title": "Groceries!", "pinned": True},
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 2
    assert resp.json()["pinned"] is True

    # stale write rejected
    resp = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=headers,
        json={"base_version": 1, "title": "Stale"},
    )
    assert resp.status_code == 409

    # soft delete → recently deleted
    assert (
        await client.delete(f"/api/v1/notes/{note['id']}", headers=headers)
    ).status_code == 204
    assert (
        await client.get(f"/api/v1/notes?folder_id={folder['id']}", headers=headers)
    ).json() == []
    deleted = (await client.get("/api/v1/notes?deleted=true", headers=headers)).json()
    assert len(deleted) == 1

    # restore
    resp = await client.post(f"/api/v1/notes/{note['id']}/restore", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["deleted_at"] is None

    # permanent delete
    assert (
        await client.delete(
            f"/api/v1/notes/{note['id']}?permanent=true", headers=headers
        )
    ).status_code == 204
    assert (
        await client.get(f"/api/v1/notes/{note['id']}", headers=headers)
    ).status_code == 404


async def test_folder_crud_and_default_protection(auth):
    client, headers, _ = auth
    default = await _default_folder(client, headers)

    resp = await client.post("/api/v1/folders", headers=headers, json={"name": "Work"})
    assert resp.status_code == 201
    work = resp.json()

    resp = await client.patch(
        f"/api/v1/folders/{work['id']}", headers=headers, json={"name": "Job"}
    )
    assert resp.json()["name"] == "Job"

    # default folder is protected
    assert (
        await client.patch(
            f"/api/v1/folders/{default['id']}", headers=headers, json={"name": "X"}
        )
    ).status_code == 400
    assert (
        await client.delete(f"/api/v1/folders/{default['id']}", headers=headers)
    ).status_code == 400

    # deleting a folder KEEPS its notes — they move to the default folder
    note = (
        await client.post(
            "/api/v1/notes",
            headers=headers,
            json={"folder_id": work["id"], "title": "t", "body_text": "t"},
        )
    ).json()
    assert (
        await client.delete(f"/api/v1/folders/{work['id']}", headers=headers)
    ).status_code == 204
    assert (await client.get("/api/v1/notes?deleted=true", headers=headers)).json() == []
    moved = (
        await client.get(f"/api/v1/notes?folder_id={default['id']}", headers=headers)
    ).json()
    assert [m["id"] for m in moved] == [note["id"]]


async def test_notes_are_private(auth):
    client, headers_a, _ = auth
    folder_a = await _default_folder(client, headers_a)
    note = (
        await client.post(
            "/api/v1/notes",
            headers=headers_a,
            json={"folder_id": folder_a["id"], "title": "secret", "body_text": "secret"},
        )
    ).json()

    from tests.conftest import make_user

    headers_b = await make_user(client, headers_a)

    # B cannot see A's note, folder list, or note by id
    assert (await client.get("/api/v1/notes", headers=headers_b)).json() == []
    assert (
        await client.get(f"/api/v1/notes/{note['id']}", headers=headers_b)
    ).status_code == 404
    assert (
        await client.post(
            "/api/v1/notes",
            headers=headers_b,
            json={"folder_id": folder_a["id"], "title": "x", "body_text": "x"},
        )
    ).status_code == 404


async def test_deleting_parent_folder_rescues_subfolder_notes(auth):
    client, headers, _ = auth
    parent = (
        await client.post("/api/v1/folders", headers=headers, json={"name": "Parent"})
    ).json()
    child = (
        await client.post(
            "/api/v1/folders",
            headers=headers,
            json={"name": "Child", "parent_id": parent["id"]},
        )
    ).json()
    note = (
        await client.post(
            "/api/v1/notes",
            headers=headers,
            json={"folder_id": child["id"], "title": "nested", "body_text": "nested"},
        )
    ).json()

    assert (
        await client.delete(f"/api/v1/folders/{parent['id']}", headers=headers)
    ).status_code == 204
    # child folder gone, note survived ALIVE in the default folder
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    assert all(f["name"] not in ("Parent", "Child") for f in folders)
    default = next(f for f in folders if f["is_default"])
    moved = (
        await client.get(f"/api/v1/notes?folder_id={default['id']}", headers=headers)
    ).json()
    assert note["id"] in [m["id"] for m in moved]
    assert (await client.get("/api/v1/notes?deleted=true", headers=headers)).json() == []


async def test_capture_creates_note_in_default_folder(auth):
    """The iOS-Shortcut capture endpoint: text in, titled + tagged note out."""
    client, alice, _ = auth
    resp = await client.post(
        "/api/v1/notes/capture",
        headers=alice,
        json={"text": "Shared headline\nSome body text #captured\n\nlast line"},
    )
    assert resp.status_code == 201, resp.text
    note = resp.json()
    assert note["title"] == "Shared headline"
    assert "#captured" in note["body_text"]
    paragraphs = note["body"]["content"]
    assert [p["type"] for p in paragraphs] == ["paragraph"] * 4
    assert paragraphs[2] == {"type": "paragraph"}  # blank line stays blank

    tags = (await client.get("/api/v1/tags", headers=alice)).json()
    assert any(t["name"] == "captured" for t in tags)

    listing = (await client.get("/api/v1/notes", headers=alice)).json()
    assert any(n["id"] == note["id"] for n in listing)


async def test_capture_requires_auth(auth):
    client, _, _ = auth
    resp = await client.post("/api/v1/notes/capture", json={"text": "hi"})
    assert resp.status_code == 401


async def test_capture_via_query_token_and_raw_text(auth, client=None):
    """The Shortcut path: token in the URL, plain-text body, no headers."""
    client, alice, _ = auth
    token = alice["Authorization"].removeprefix("Bearer ")
    resp = await client.post(
        f"/api/v1/notes/capture?token={token}",
        content="Raw capture line\nsecond line",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["title"] == "Raw capture line"

    bad = await client.post(
        "/api/v1/notes/capture?token=wrong",
        content="x",
        headers={"Content-Type": "text/plain"},
    )
    assert bad.status_code == 401

"""Public secret links: create/revoke, anonymous read + edit, history."""

from tests.conftest import make_user


async def _default_folder_id(client, headers):
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    return next(f["id"] for f in folders if f["is_default"])


async def _make_note(client, headers, folder_id, text="Recipe\nflour and water"):
    resp = await client.post(
        "/api/v1/notes",
        headers=headers,
        json={"folder_id": folder_id, "title": text.split("\n")[0], "body_text": text},
    )
    assert resp.status_code == 201
    return resp.json()


async def _note_with_link(auth, role="editor"):
    client, alice, _ = auth
    fid = await _default_folder_id(client, alice)
    note = await _make_note(client, alice, fid)
    link = (
        await client.put(
            f"/api/v1/notes/{note['id']}/link", headers=alice, json={"role": role}
        )
    ).json()
    return client, alice, note, link


async def test_link_lifecycle(auth):
    client, alice, note, link = await _note_with_link(auth)
    assert link["role"] == "editor"
    assert len(link["token"]) >= 20

    # get returns the same link; upsert changes role but keeps the token
    same = (
        await client.get(f"/api/v1/notes/{note['id']}/link", headers=alice)
    ).json()
    assert same["token"] == link["token"]
    changed = (
        await client.put(
            f"/api/v1/notes/{note['id']}/link", headers=alice, json={"role": "viewer"}
        )
    ).json()
    assert changed == {"token": link["token"], "role": "viewer"}

    # revoke kills it
    assert (
        await client.delete(f"/api/v1/notes/{note['id']}/link", headers=alice)
    ).status_code == 204
    assert (
        await client.get(f"/api/v1/public/notes/{link['token']}")
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/notes/{note['id']}/link", headers=alice)
    ).json() is None


async def test_anonymous_read(auth):
    client, alice, note, link = await _note_with_link(auth, role="viewer")
    resp = await client.get(f"/api/v1/public/notes/{link['token']}")  # no auth!
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Recipe"
    assert data["role"] == "viewer"
    assert data["app_name"]  # branding for the public page

    assert (await client.get("/api/v1/public/notes/not-a-real-token")).status_code == 404


async def test_edit_requires_a_name(auth):
    client, alice, note, link = await _note_with_link(auth, role="editor")
    # no name → refused; blank name → refused
    for body in (
        {"base_version": note["version"], "body_text": "x"},
        {"base_version": note["version"], "guest_name": "   ", "body_text": "x"},
    ):
        resp = await client.patch(f"/api/v1/public/notes/{link['token']}", json=body)
        assert resp.status_code == 400

    # with a name → accepted, attributed
    resp = await client.patch(
        f"/api/v1/public/notes/{link['token']}",
        json={
            "base_version": note["version"],
            "guest_name": "Dana",
            "title": "Recipe",
            "body_text": "Recipe\nflour, water, and yeast",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == note["version"] + 1

    # stale write still rejected (with a name)
    assert (
        await client.patch(
            f"/api/v1/public/notes/{link['token']}",
            json={"base_version": note["version"], "guest_name": "Dana", "body_text": "c"},
        )
    ).status_code == 409

    revs = (
        await client.get(f"/api/v1/notes/{note['id']}/revisions", headers=alice)
    ).json()
    assert revs[0]["editor_name"] == "Dana (guest)"


async def test_named_guests_are_distinguished(auth):
    client, alice, note, link = await _note_with_link(auth, role="editor")
    token = link["token"]

    async def guest_edit(name, text, base):
        resp = await client.patch(
            f"/api/v1/public/notes/{token}",
            json={"base_version": base, "guest_name": name, "body_text": text},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["version"]

    v = await guest_edit("Sue", "Recipe\nsue one", note["version"])
    v = await guest_edit("Sue", "Recipe\nsue two", v)  # same guest → coalesces
    v = await guest_edit("Mike", "Recipe\nmike here", v)  # different guest → new session

    revs = (
        await client.get(f"/api/v1/notes/{note['id']}/revisions", headers=alice)
    ).json()
    # newest first: Mike, Sue, and the owner's creation snapshot
    assert [r["editor_name"] for r in revs] == [
        "Mike (guest)",
        "Sue (guest)",
        "Alice",
    ]


async def test_view_link_cannot_edit(auth):
    client, alice, note, link = await _note_with_link(auth, role="viewer")
    resp = await client.patch(
        f"/api/v1/public/notes/{link['token']}",
        json={"base_version": note["version"], "body_text": "nope"},
    )
    assert resp.status_code == 403


async def test_only_owner_manages_link(auth):
    client, alice, note, link = await _note_with_link(auth)
    bob = await make_user(client, alice)
    await client.put(
        f"/api/v1/notes/{note['id']}/shares",
        headers=alice,
        json={"username": "bob", "role": "editor"},
    )
    # even an editor cannot see or mint the public link
    assert (
        await client.get(f"/api/v1/notes/{note['id']}/link", headers=bob)
    ).status_code == 404
    assert (
        await client.put(
            f"/api/v1/notes/{note['id']}/link", headers=bob, json={"role": "editor"}
        )
    ).status_code == 404


async def test_deleted_note_link_dead(auth):
    client, alice, note, link = await _note_with_link(auth)
    await client.delete(f"/api/v1/notes/{note['id']}", headers=alice)  # soft delete
    assert (
        await client.get(f"/api/v1/public/notes/{link['token']}")
    ).status_code == 404

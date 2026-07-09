"""Locked notes: lock/unlock lifecycle, exclusions, and access rules.

The "cipher" here is opaque to the server — tests just use a marker
string, since the server must never interpret it.
"""

from tests.conftest import make_user

CIPHER = '{"v":1,"salt":"c2FsdA==","iv":"aXY=","ct":"Y2lwaGVydGV4dA=="}'


async def _default_folder_id(client, headers):
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    return next(f["id"] for f in folders if f["is_default"])


async def _make_note(client, headers, folder_id, title="Diary", text="Diary\n#private secrets"):
    resp = await client.post(
        "/api/v1/notes",
        headers=headers,
        json={"folder_id": folder_id, "title": title, "body_text": text,
              "body": {"type": "doc", "content": []}},
    )
    assert resp.status_code == 201
    return resp.json()


async def _lock(client, headers, note, version=None):
    resp = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=headers,
        json={"base_version": version or note["version"], "locked": True, "cipher_body": CIPHER},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_lock_clears_plaintext_and_tags(auth):
    client, alice, _ = auth
    fid = await _default_folder_id(client, alice)
    note = await _make_note(client, alice, fid)
    assert [t["name"] for t in (await client.get("/api/v1/tags", headers=alice)).json()] == ["private"]

    locked = await _lock(client, alice, note)
    assert locked["locked"] is True
    assert locked["body"] is None
    assert locked["body_text"] == ""
    assert locked["cipher_body"] == CIPHER
    assert locked["title"] == "Diary"  # title stays visible, like iOS

    # tags gone, body search finds nothing, title search still works
    assert (await client.get("/api/v1/tags", headers=alice)).json() == []
    assert (await client.get("/api/v1/search?q=secrets", headers=alice)).json() == []
    hits = (await client.get("/api/v1/search?q=diary", headers=alice)).json()
    assert [h["preview"] for h in hits] == ["Locked"]


async def test_locked_note_rejects_plaintext_edit_but_accepts_cipher(auth):
    client, alice, _ = auth
    fid = await _default_folder_id(client, alice)
    note = await _lock(client, alice, await _make_note(client, alice, fid))

    resp = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=alice,
        json={"base_version": note["version"], "body_text": "sneaky plaintext"},
    )
    assert resp.status_code == 400

    resp = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=alice,
        json={"base_version": note["version"], "cipher_body": CIPHER + "2"},
    )
    assert resp.status_code == 200
    assert resp.json()["cipher_body"] == CIPHER + "2"


async def test_unlock_restores_content_and_tags(auth):
    client, alice, _ = auth
    fid = await _default_folder_id(client, alice)
    note = await _lock(client, alice, await _make_note(client, alice, fid))

    resp = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=alice,
        json={
            "base_version": note["version"],
            "locked": False,
            "body": {"type": "doc", "content": []},
            "body_text": "Diary\n#private secrets",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["locked"] is False and data["cipher_body"] is None
    assert [t["name"] for t in (await client.get("/api/v1/tags", headers=alice)).json()] == ["private"]
    assert len((await client.get("/api/v1/search?q=secrets", headers=alice)).json()) == 1


async def test_cannot_lock_shared_or_linked_note(auth):
    client, alice, _ = auth
    await make_user(client, alice)
    fid = await _default_folder_id(client, alice)

    shared = await _make_note(client, alice, fid, title="Shared")
    await client.put(
        f"/api/v1/notes/{shared['id']}/shares",
        headers=alice,
        json={"username": "bob", "role": "viewer"},
    )
    resp = await client.patch(
        f"/api/v1/notes/{shared['id']}",
        headers=alice,
        json={"base_version": shared["version"], "locked": True, "cipher_body": CIPHER},
    )
    assert resp.status_code == 400

    linked = await _make_note(client, alice, fid, title="Linked")
    await client.put(
        f"/api/v1/notes/{linked['id']}/link", headers=alice, json={"role": "viewer"}
    )
    resp = await client.patch(
        f"/api/v1/notes/{linked['id']}",
        headers=alice,
        json={"base_version": linked["version"], "locked": True, "cipher_body": CIPHER},
    )
    assert resp.status_code == 400


async def test_cannot_share_or_link_locked_note(auth):
    client, alice, _ = auth
    await make_user(client, alice)
    fid = await _default_folder_id(client, alice)
    note = await _lock(client, alice, await _make_note(client, alice, fid))

    assert (
        await client.put(
            f"/api/v1/notes/{note['id']}/shares",
            headers=alice,
            json={"username": "bob", "role": "viewer"},
        )
    ).status_code == 400
    assert (
        await client.put(
            f"/api/v1/notes/{note['id']}/link", headers=alice, json={"role": "viewer"}
        )
    ).status_code == 400


async def test_locked_note_invisible_in_shared_folder(auth):
    client, alice, _ = auth
    bob = await make_user(client, alice)
    fid = await _default_folder_id(client, alice)
    note = await _make_note(client, alice, fid)
    await client.put(
        f"/api/v1/folders/{fid}/shares",
        headers=alice,
        json={"username": "bob", "role": "editor"},
    )
    # visible before locking…
    assert len((await client.get(f"/api/v1/notes?folder_id={fid}", headers=bob)).json()) == 1
    await _lock(client, alice, note)
    # …gone after, everywhere bob could look
    assert (await client.get(f"/api/v1/notes?folder_id={fid}", headers=bob)).json() == []
    assert (await client.get("/api/v1/notes?shared=true", headers=bob)).json() == []
    assert (await client.get(f"/api/v1/notes/{note['id']}", headers=bob)).status_code == 404
    sync = (await client.get("/api/v1/notes/sync", headers=bob)).json()
    assert sync == []


async def test_own_locked_note_syncs_as_cipher_and_history_pauses(auth):
    client, alice, _ = auth
    fid = await _default_folder_id(client, alice)
    note = await _lock(client, alice, await _make_note(client, alice, fid))

    sync = (await client.get("/api/v1/notes/sync", headers=alice)).json()
    mine = next(n for n in sync if n["id"] == note["id"])
    assert mine["locked"] is True and mine["cipher_body"] == CIPHER
    assert mine["body"] is None

    # cipher re-saves add no history entries
    before = (await client.get(f"/api/v1/notes/{note['id']}/revisions", headers=alice)).json()
    await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=alice,
        json={"base_version": note["version"], "cipher_body": CIPHER + "3"},
    )
    after = (await client.get(f"/api/v1/notes/{note['id']}/revisions", headers=alice)).json()
    assert len(after) == len(before)

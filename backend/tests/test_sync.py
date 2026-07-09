"""/notes/sync — full-body hydration for the offline cache."""

from tests.conftest import make_user


async def _default_folder_id(client, headers):
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    return next(f["id"] for f in folders if f["is_default"])


async def test_sync_returns_full_own_and_shared_notes(auth):
    client, alice, _ = auth
    bob = await make_user(client, alice)
    fid_a = await _default_folder_id(client, alice)
    fid_b = await _default_folder_id(client, bob)

    body = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Mine"}]}]}
    await client.post(
        "/api/v1/notes",
        headers=alice,
        json={"folder_id": fid_a, "title": "Mine", "body": body, "body_text": "Mine"},
    )
    shared = (
        await client.post(
            "/api/v1/notes",
            headers=bob,
            json={"folder_id": fid_b, "title": "Bobs", "body": body, "body_text": "Bobs"},
        )
    ).json()
    await client.put(
        f"/api/v1/notes/{shared['id']}/shares",
        headers=bob,
        json={"username": "alice", "role": "editor"},
    )
    # a deleted note must NOT hydrate
    gone = (
        await client.post(
            "/api/v1/notes",
            headers=alice,
            json={"folder_id": fid_a, "title": "Gone", "body_text": "Gone"},
        )
    ).json()
    await client.delete(f"/api/v1/notes/{gone['id']}", headers=alice)

    data = (await client.get("/api/v1/notes/sync", headers=alice)).json()
    by_title = {n["title"]: n for n in data}
    assert set(by_title) == {"Mine", "Bobs"}
    assert by_title["Mine"]["role"] == "owner"
    assert by_title["Mine"]["body"] is not None  # full body present
    assert by_title["Bobs"]["role"] == "editor"
    assert by_title["Bobs"]["owner_name"] == "Bob"


async def test_import_round_trip(auth):
    """Export shape → /notes/import: folders recreated, timestamps kept,
    locked ciphertext passes through, tags re-derived."""
    client, alice, _ = auth
    payload = {
        "notes": [
            {
                "folder": "Recipes",
                "title": "Soup",
                "body": {"type": "doc", "content": [{"type": "paragraph",
                    "content": [{"type": "text", "text": "Soup #dinner"}]}]},
                "body_text": "Soup #dinner",
                "created_at": "2022-05-01T08:00:00Z",
                "updated_at": "2022-06-01T08:00:00Z",
                "pinned": True,
            },
            {
                "folder": "Private",
                "title": "Sealed",
                "locked": True,
                "cipher_body": '{"v":1,"salt":"x","iv":"y","ct":"z"}',
                "created_at": "2023-01-01T00:00:00Z",
                "updated_at": "2023-01-01T00:00:00Z",
            },
        ]
    }
    resp = await client.post("/api/v1/notes/import", headers=alice, json=payload)
    assert resp.status_code == 201, resp.text
    assert resp.json()["imported"] == 2

    folders = {f["name"]: f for f in (await client.get("/api/v1/folders", headers=alice)).json()}
    assert "Recipes" in folders and "Private" in folders

    soup = (await client.get(f"/api/v1/notes?folder_id={folders['Recipes']['id']}", headers=alice)).json()[0]
    assert soup["pinned"] is True
    assert soup["updated_at"].startswith("2022-06-01")
    tags = [t["name"] for t in (await client.get("/api/v1/tags", headers=alice)).json()]
    assert "dinner" in tags

    sealed = (await client.get(f"/api/v1/notes?folder_id={folders['Private']['id']}", headers=alice)).json()[0]
    assert sealed["locked"] is True and sealed["preview"] == "Locked"

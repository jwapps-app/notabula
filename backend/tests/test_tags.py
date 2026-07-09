"""Tags: extraction from body_text, counts, filtering, orphan cleanup."""

from app.services.tags import extract_tag_names


def test_extract_tag_names():
    assert extract_tag_names("Buy #groceries and #Work stuff") == {"groceries", "work"}
    assert extract_tag_names("#to-do list #to-do again") == {"to-do"}
    assert extract_tag_names("no tags here; #123 isn't one either") == set()
    assert extract_tag_names("") == set()


async def _default_folder_id(client, headers):
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    return next(f["id"] for f in folders if f["is_default"])


async def _make_note(client, headers, folder_id, text):
    resp = await client.post(
        "/api/v1/notes",
        headers=headers,
        json={"folder_id": folder_id, "title": text.split("\n")[0], "body_text": text},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_tags_created_and_counted(auth):
    client, headers, _ = auth
    fid = await _default_folder_id(client, headers)
    await _make_note(client, headers, fid, "Trip\npack bags #travel #packing")
    await _make_note(client, headers, fid, "Flights\nbook flights #travel")

    tags = (await client.get("/api/v1/tags", headers=headers)).json()
    assert [(t["name"], t["note_count"]) for t in tags] == [
        ("packing", 1),
        ("travel", 2),
    ]


async def test_filter_notes_by_tag(auth):
    client, headers, _ = auth
    fid = await _default_folder_id(client, headers)
    other = (
        await client.post("/api/v1/folders", headers=headers, json={"name": "Work"})
    ).json()
    await _make_note(client, headers, fid, "Trip\n#travel")
    await _make_note(client, headers, other["id"], "Offsite\nplan the offsite #travel")
    await _make_note(client, headers, fid, "Untagged\nnothing")

    # tag view spans folders
    hits = (await client.get("/api/v1/notes?tag=travel", headers=headers)).json()
    assert sorted(h["title"] for h in hits) == ["Offsite", "Trip"]


async def test_tag_sync_and_orphan_cleanup(auth):
    client, headers, _ = auth
    fid = await _default_folder_id(client, headers)
    note = await _make_note(client, headers, fid, "Ideas\n#draft #keep")

    # editing away a hashtag removes the tag; orphans disappear entirely
    resp = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=headers,
        json={"base_version": 1, "body_text": "Ideas\n#keep only"},
    )
    assert resp.status_code == 200
    tags = (await client.get("/api/v1/tags", headers=headers)).json()
    assert [t["name"] for t in tags] == ["keep"]


async def test_deleted_notes_leave_tag_counts(auth):
    client, headers, _ = auth
    fid = await _default_folder_id(client, headers)
    note = await _make_note(client, headers, fid, "Trip\n#travel")
    await client.delete(f"/api/v1/notes/{note['id']}", headers=headers)
    tags = (await client.get("/api/v1/tags", headers=headers)).json()
    assert tags == []


async def test_tags_are_private(auth):
    client, headers_a, _ = auth
    fid = await _default_folder_id(client, headers_a)
    await _make_note(client, headers_a, fid, "Mine\n#secret")

    from tests.conftest import make_user

    headers_b = await make_user(client, headers_a)
    assert (await client.get("/api/v1/tags", headers=headers_b)).json() == []
    assert (
        await client.get("/api/v1/notes?tag=secret", headers=headers_b)
    ).json() == []


async def test_rename_tag_rewrites_every_note(auth):
    client, headers, _ = auth
    fid = await _default_folder_id(client, headers)
    a = await _make_note(client, headers, fid, "Trip\npack #Travel gear")
    b = await _make_note(client, headers, fid, "Flights\n#travel and #traveler notes")

    resp = await client.post(
        "/api/v1/tags/travel/rename", headers=headers, json={"new_name": "vacation"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 2

    tags = [t["name"] for t in (await client.get("/api/v1/tags", headers=headers)).json()]
    assert "vacation" in tags and "travel" not in tags
    assert "traveler" in tags  # partial matches untouched

    note_a = (await client.get(f"/api/v1/notes/{a['id']}", headers=headers)).json()
    assert "#vacation" in note_a["body_text"] and "#Travel" not in note_a["body_text"]
    note_b = (await client.get(f"/api/v1/notes/{b['id']}", headers=headers)).json()
    assert "#vacation" in note_b["body_text"] and "#traveler" in note_b["body_text"]
    # versions bumped so other devices resync rather than clobber
    assert note_a["version"] == 2


async def test_rename_tag_validation(auth):
    client, headers, _ = auth
    fid = await _default_folder_id(client, headers)
    await _make_note(client, headers, fid, "X\n#keep")
    assert (
        await client.post(
            "/api/v1/tags/keep/rename", headers=headers, json={"new_name": "has space"}
        )
    ).status_code == 422
    assert (
        await client.post(
            "/api/v1/tags/ghost/rename", headers=headers, json={"new_name": "ok"}
        )
    ).status_code == 404

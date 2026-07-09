"""Smart views: media / links / tasks / locked / recent."""

CIPHER = '{"v":1,"salt":"c2FsdA==","iv":"aXY=","ct":"Y2lwaGVydGV4dA=="}'


async def _default_folder_id(client, headers):
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    return next(f["id"] for f in folders if f["is_default"])


async def _note(client, headers, fid, title, body=None, text=None):
    resp = await client.post(
        "/api/v1/notes",
        headers=headers,
        json={
            "folder_id": fid,
            "title": title,
            "body": body or {"type": "doc", "content": []},
            "body_text": text or title,
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def test_smart_views(auth):
    client, alice, _ = auth
    fid = await _default_folder_id(client, alice)

    await _note(
        client, alice, fid, "With image",
        body={"type": "doc", "content": [
            {"type": "image", "attrs": {"src": "/media/attachments/x.png"}}]},
    )
    await _note(client, alice, fid, "With link", text="With link\nsee https://example.com")
    await _note(
        client, alice, fid, "With todo",
        body={"type": "doc", "content": [
            {"type": "taskList", "content": [
                {"type": "taskItem", "attrs": {"checked": False},
                 "content": [{"type": "paragraph"}]}]}]},
    )
    await _note(
        client, alice, fid, "All done",
        body={"type": "doc", "content": [
            {"type": "taskList", "content": [
                {"type": "taskItem", "attrs": {"checked": True},
                 "content": [{"type": "paragraph"}]}]}]},
    )
    plain = await _note(client, alice, fid, "Plain")
    locked = await _note(client, alice, fid, "Secret")
    await client.patch(
        f"/api/v1/notes/{locked['id']}",
        headers=alice,
        json={"base_version": 1, "locked": True, "cipher_body": CIPHER},
    )

    async def titles(view):
        resp = await client.get(f"/api/v1/notes?view={view}", headers=alice)
        assert resp.status_code == 200, resp.text
        return sorted(n["title"] for n in resp.json())

    assert await titles("media") == ["With image"]
    assert await titles("links") == ["With link"]
    assert await titles("tasks") == ["With todo"]  # checked-only note excluded
    assert await titles("locked") == ["Secret"]
    # everything was just created → all in recent
    assert "Plain" in await titles("recent")
    assert plain["title"] == "Plain"

    assert (await client.get("/api/v1/notes?view=bogus", headers=alice)).status_code == 422

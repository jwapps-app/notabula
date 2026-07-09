"""Admin user management: list, create, password reset, delete, 2FA clear."""

import pyotp

from tests.conftest import make_user


async def test_list_users(auth):
    client, admin_headers, _ = auth
    await make_user(client, admin_headers)
    users = (await client.get("/api/v1/admin/users", headers=admin_headers)).json()
    assert [(u["username"], u["is_admin"]) for u in users] == [
        ("alice", True),
        ("bob", False),
    ]


async def test_created_user_gets_default_folder(auth):
    client, admin_headers, _ = auth
    headers_b = await make_user(client, admin_headers)
    folders = (await client.get("/api/v1/folders", headers=headers_b)).json()
    assert any(f["is_default"] and f["name"] == "Notes" for f in folders)


async def test_password_reset_revokes_sessions(auth):
    client, admin_headers, _ = auth
    headers_b = await make_user(client, admin_headers)
    users = (await client.get("/api/v1/admin/users", headers=admin_headers)).json()
    bob_id = next(u["id"] for u in users if u["username"] == "bob")

    resp = await client.post(
        f"/api/v1/admin/users/{bob_id}/password",
        headers=admin_headers,
        json={"password": "newpassword456"},
    )
    assert resp.status_code == 204

    # old session dead, old password dead, new password works
    assert (await client.get("/api/v1/auth/me", headers=headers_b)).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "bob", "password": "password123"},
        )
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "bob", "password": "newpassword456"},
        )
    ).status_code == 200


async def test_admin_clears_lost_totp(auth):
    client, admin_headers, _ = auth
    headers_b = await make_user(client, admin_headers)

    # bob enables 2FA…
    setup = (
        await client.post("/api/v1/auth/totp/setup", headers=headers_b)
    ).json()
    code = pyotp.TOTP(setup["secret"]).now()
    assert (
        await client.post(
            "/api/v1/auth/totp/enable", headers=headers_b, json={"code": code}
        )
    ).status_code == 200

    # …loses his phone; admin clears it; plain login works again
    users = (await client.get("/api/v1/admin/users", headers=admin_headers)).json()
    bob_id = next(u["id"] for u in users if u["username"] == "bob")
    resp = await client.post(
        f"/api/v1/admin/users/{bob_id}/totp/disable", headers=admin_headers
    )
    assert resp.status_code == 204
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "bob", "password": "password123"}
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["totp_enabled"] is False


async def test_delete_user_but_never_self(auth):
    client, admin_headers, admin_user = auth
    await make_user(client, admin_headers)
    users = (await client.get("/api/v1/admin/users", headers=admin_headers)).json()
    bob_id = next(u["id"] for u in users if u["username"] == "bob")

    assert (
        await client.delete(
            f"/api/v1/admin/users/{admin_user['id']}", headers=admin_headers
        )
    ).status_code == 400

    assert (
        await client.delete(f"/api/v1/admin/users/{bob_id}", headers=admin_headers)
    ).status_code == 204
    users = (await client.get("/api/v1/admin/users", headers=admin_headers)).json()
    assert [u["username"] for u in users] == ["alice"]


async def test_bulk_import_preserves_timestamps_and_tags(auth):
    client, admin_headers, _ = auth
    resp = await client.post(
        "/api/v1/admin/import",
        headers=admin_headers,
        json={
            "folder_name": "Memos",
            "notes": [
                {
                    "title": "Old thought",
                    "body": {"type": "doc", "content": [{"type": "paragraph",
                        "content": [{"type": "text", "text": "Old thought #Prayer"}]}]},
                    "body_text": "Old thought #Prayer",
                    "created_at": "2023-02-01T10:00:00Z",
                    "updated_at": "2023-02-01T10:00:00Z",
                }
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["imported"] == 1

    folders = (await client.get("/api/v1/folders", headers=admin_headers)).json()
    memos = next(f for f in folders if f["name"] == "Memos")
    notes = (
        await client.get(f"/api/v1/notes?folder_id={memos['id']}", headers=admin_headers)
    ).json()
    assert notes[0]["updated_at"].startswith("2023-02-01")
    tags = (await client.get("/api/v1/tags", headers=admin_headers)).json()
    assert [t["name"] for t in tags] == ["prayer"]


async def test_bulk_import_admin_only(auth):
    from tests.conftest import make_user

    client, admin_headers, _ = auth
    bob = await make_user(client, admin_headers)
    resp = await client.post(
        "/api/v1/admin/import",
        headers=bob,
        json={"folder_name": "X", "notes": []},
    )
    assert resp.status_code == 403

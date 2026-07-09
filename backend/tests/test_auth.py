"""Auth flow: register, login, me, logout, first-user-is-admin."""


async def test_register_first_user_is_admin(auth):
    _, _, user = auth
    assert user["is_admin"] is True


async def test_registration_closes_after_first_user(auth):
    client, _, _ = auth
    resp = await client.post(
        "/api/v1/auth/register",
        json={"username": "bob", "name": "Bob", "password": "password123"},
    )
    assert resp.status_code == 403
    # /meta reflects it so the UI can hide the register link
    meta = (await client.get("/api/v1/meta")).json()
    assert meta["allow_registration"] is False


async def test_meta_reports_open_registration_on_fresh_instance(client):
    meta = (await client.get("/api/v1/meta")).json()
    assert meta["allow_registration"] is True


async def test_admin_created_user_not_admin(auth):
    from tests.conftest import make_user

    client, admin_headers, _ = auth
    headers_b = await make_user(client, admin_headers)
    me = (await client.get("/api/v1/auth/me", headers=headers_b)).json()
    assert me["is_admin"] is False
    assert me["username"] == "bob"


async def test_admin_duplicate_username_rejected(auth):
    client, admin_headers, _ = auth
    resp = await client.post(
        "/api/v1/admin/users",
        headers=admin_headers,
        json={"username": "alice", "name": "Dup", "password": "password123"},
    )
    assert resp.status_code == 409


async def test_non_admin_cannot_manage_users(auth):
    from tests.conftest import make_user

    client, admin_headers, _ = auth
    headers_b = await make_user(client, admin_headers)
    assert (
        await client.get("/api/v1/admin/users", headers=headers_b)
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/admin/users",
            headers=headers_b,
            json={"username": "eve", "name": "Eve", "password": "password123"},
        )
    ).status_code == 403


async def test_short_password_rejected(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"username": "carol", "name": "C", "password": "short"},
    )
    assert resp.status_code == 422


async def test_login_and_me(auth):
    client, _, _ = auth
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password123"},
    )
    assert resp.status_code == 200
    token = resp.json()["session_token"]

    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


async def test_bad_password(auth):
    client, _, _ = auth
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "wrong-password"},
    )
    assert resp.status_code == 401


async def test_logout_invalidates_session(auth):
    client, headers, _ = auth
    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401


async def test_unauthenticated_rejected(client):
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/notes")).status_code == 401


async def test_invalid_usernames_rejected(client):
    for bad in ["ab", "has space", "Wei#rd", "-leadingdash", "x" * 33]:
        resp = await client.post(
            "/api/v1/auth/register",
            json={"username": bad, "name": "X", "password": "password123"},
        )
        assert resp.status_code == 422, f"{bad!r} should be rejected"


async def test_username_normalized_lowercase(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"username": "  Dave  ", "name": "Dave", "password": "password123"},
    )
    assert resp.status_code == 201
    assert resp.json()["user"]["username"] == "dave"
    # login works with any casing
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "DAVE", "password": "password123"},
    )
    assert resp.status_code == 200

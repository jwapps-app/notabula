"""TOTP two-factor: enrollment, login gate, recovery codes, disable."""

import pyotp


async def _enroll(client, headers):
    """Run setup + enable; return (secret, recovery_codes)."""
    setup = (await client.post("/api/v1/auth/totp/setup", headers=headers)).json()
    secret = setup["secret"]
    code = pyotp.TOTP(secret).now()
    resp = await client.post(
        "/api/v1/auth/totp/enable", headers=headers, json={"code": code}
    )
    assert resp.status_code == 200, resp.text
    return secret, resp.json()["recovery_codes"]


async def test_setup_returns_qr_and_uri(auth):
    client, headers, _ = auth
    resp = await client.post("/api/v1/auth/totp/setup", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["otpauth_uri"].startswith("otpauth://totp/")
    assert len(data["qr_png_base64"]) > 100
    # not yet enabled — login still works without a code
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password123"},
    )
    assert resp.status_code == 200


async def test_enable_requires_valid_code(auth):
    client, headers, _ = auth
    await client.post("/api/v1/auth/totp/setup", headers=headers)
    resp = await client.post(
        "/api/v1/auth/totp/enable", headers=headers, json={"code": "000000"}
    )
    assert resp.status_code == 401


async def test_login_requires_totp_once_enabled(auth):
    client, headers, _ = auth
    secret, recovery = await _enroll(client, headers)
    assert len(recovery) == 10

    # no code → machine-readable sentinel
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password123"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "totp_required"

    # wrong code
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password123", "totp_code": "000000"},
    )
    assert resp.status_code == 401

    # correct code
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "username": "alice",
            "password": "password123",
            "totp_code": pyotp.TOTP(secret).now(),
        },
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["totp_enabled"] is True


async def test_recovery_code_single_use(auth):
    client, headers, _ = auth
    _, recovery = await _enroll(client, headers)
    code = recovery[0]

    login = {"username": "alice", "password": "password123", "totp_code": code}
    assert (await client.post("/api/v1/auth/login", json=login)).status_code == 200
    # second use of the same code fails
    assert (await client.post("/api/v1/auth/login", json=login)).status_code == 401


async def test_disable_requires_code_and_restores_plain_login(auth):
    client, headers, _ = auth
    secret, _ = await _enroll(client, headers)

    resp = await client.post(
        "/api/v1/auth/totp/disable", headers=headers, json={"code": "000000"}
    )
    assert resp.status_code == 401

    resp = await client.post(
        "/api/v1/auth/totp/disable",
        headers=headers,
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert resp.status_code == 204

    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password123"},
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["totp_enabled"] is False


async def test_wrong_password_never_reveals_totp_state(auth):
    client, headers, _ = auth
    await _enroll(client, headers)
    # bad password + 2FA account → generic error, not totp_required
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "wrong-password"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Incorrect username or password"

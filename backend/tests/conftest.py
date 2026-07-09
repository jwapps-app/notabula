"""Test fixtures — in-memory SQLite via aiosqlite, app served by httpx."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app


@pytest.fixture
async def client():
    # The login throttle is process-local state — start each test clean.
    from app.routers.auth import _login_failures

    _login_failures.clear()

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    TestSession = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with TestSession() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture
async def auth(client):
    """Register the bootstrap (admin) user; return (client, headers, user)."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "name": "Alice", "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['session_token']}"}
    return client, headers, data["user"]


async def make_user(client, admin_headers, username="bob", name="Bob"):
    """Admin-create a second user and log them in; return their headers.

    Registration closes after the first account, so this is the only way
    tests (like production) get additional users.
    """
    resp = await client.post(
        "/api/v1/admin/users",
        headers=admin_headers,
        json={"username": username, "name": name, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['session_token']}"}

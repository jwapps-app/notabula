"""Push pipeline: registration, event emission, coalescing, reminders.

Transport is stubbed at services.push.deliver — these tests assert WHO
gets notified and WHEN, not APNs/Web Push wire formats.
"""

from datetime import datetime, timedelta, timezone

import pytest

import app.services.push as push_svc
from tests.conftest import make_user


@pytest.fixture
def sent(monkeypatch):
    """Capture deliver() calls as (user targets snapshot, title, body, data)."""
    calls: list[dict] = []

    def fake_deliver(targets, *, title, body, data=None):
        calls.append(
            {
                "devices": list(targets.devices),
                "subs": [s["endpoint"] for s in targets.subscriptions],
                "title": title,
                "body": body,
                "data": data or {},
            }
        )

    monkeypatch.setattr(push_svc, "deliver", fake_deliver)
    return calls


async def _register_device(client, headers, token="tok-1", sandbox=True):
    resp = await client.post(
        "/api/v1/push/devices",
        headers=headers,
        json={"token": token, "sandbox": sandbox},
    )
    assert resp.status_code == 204


async def test_device_and_subscription_registration(auth):
    client, alice, _ = auth
    await _register_device(client, alice)

    resp = await client.post(
        "/api/v1/push/subscriptions",
        headers=alice,
        json={
            "endpoint": "https://push.example/abc",
            "keys": {"p256dh": "k1", "auth": "a1"},
        },
    )
    assert resp.status_code == 204

    # Idempotent re-register + unregister both succeed.
    await _register_device(client, alice)
    resp = await client.delete("/api/v1/push/devices/tok-1", headers=alice)
    assert resp.status_code == 204
    resp = await client.post(
        "/api/v1/push/subscriptions/delete",
        headers=alice,
        json={"endpoint": "https://push.example/abc"},
    )
    assert resp.status_code == 204


async def test_vapid_key_available(auth, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    monkeypatch.setattr(push_svc, "_VAPID_CACHE", None)
    client, alice, _ = auth
    resp = await client.get("/api/v1/push/vapid-public-key", headers=alice)
    assert resp.status_code == 200
    key = resp.json()["public_key"]
    assert len(key) > 40
    # Persisted and stable across calls.
    resp2 = await client.get("/api/v1/push/vapid-public-key", headers=alice)
    assert resp2.json()["public_key"] == key


async def _make_note(client, headers, title="Trip plans"):
    folders = (await client.get("/api/v1/folders", headers=headers)).json()
    resp = await client.post(
        "/api/v1/notes",
        headers=headers,
        json={
            "folder_id": folders[0]["id"],
            "title": title,
            "body": {"type": "doc", "content": []},
            "body_text": title,
        },
    )
    return resp.json()


async def test_share_notifies_grantee(auth, sent):
    client, alice, _ = auth
    bob = await make_user(client, alice)
    note = await _make_note(client, alice)

    resp = await client.put(
        f"/api/v1/notes/{note['id']}/shares",
        headers=alice,
        json={"username": "bob", "role": "editor"},
    )
    assert resp.status_code == 200
    assert len(sent) == 1
    assert sent[0]["title"] == "Shared with you"
    assert "Trip plans" in sent[0]["body"]

    # Changing the role of an existing share is NOT a new notification.
    await client.put(
        f"/api/v1/notes/{note['id']}/shares",
        headers=alice,
        json={"username": "bob", "role": "viewer"},
    )
    assert len(sent) == 1
    # bob edits should work below
    del bob


async def test_edit_notifies_other_participants_once_per_session(auth, sent):
    client, alice, _ = auth
    bob_headers = await make_user(client, alice)
    note = await _make_note(client, alice, title="Meal plan")
    await client.put(
        f"/api/v1/notes/{note['id']}/shares",
        headers=alice,
        json={"username": "bob", "role": "editor"},
    )
    sent.clear()

    # Bob edits → alice (owner) notified once.
    resp = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=bob_headers,
        json={
            "base_version": note["version"],
            "body": {"type": "doc", "content": []},
            "body_text": "Meal plan v2",
            "title": "Meal plan",
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(sent) == 1
    assert sent[0]["body"] == "Bob made changes"

    # A second save in the same editing session coalesces — no second push.
    v = resp.json()["version"]
    resp = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=bob_headers,
        json={
            "base_version": v,
            "body": {"type": "doc", "content": []},
            "body_text": "Meal plan v3",
            "title": "Meal plan",
        },
    )
    assert resp.status_code == 200
    assert len(sent) == 1

    # Owner editing their own unshared... this note IS shared: alice's edit
    # notifies bob (the other participant), never alice herself.
    v = resp.json()["version"]
    resp = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=alice,
        json={
            "base_version": v,
            "body": {"type": "doc", "content": []},
            "body_text": "Meal plan v4",
            "title": "Meal plan",
        },
    )
    assert resp.status_code == 200
    assert len(sent) == 2
    assert sent[1]["body"] == "Alice made changes"


async def test_unshared_note_edit_notifies_nobody(auth, sent):
    client, alice, _ = auth
    note = await _make_note(client, alice, title="Private")
    resp = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=alice,
        json={
            "base_version": note["version"],
            "body": {"type": "doc", "content": []},
            "body_text": "still private",
            "title": "Private",
        },
    )
    assert resp.status_code == 200
    assert sent == []


async def test_guest_edit_notifies_owner(auth, sent):
    client, alice, _ = auth
    note = await _make_note(client, alice, title="Party list")
    link = (
        await client.put(
            f"/api/v1/notes/{note['id']}/link",
            headers=alice,
            json={"role": "editor"},
        )
    ).json()

    resp = await client.patch(
        f"/api/v1/public/notes/{link['token']}",
        json={
            "base_version": note["version"],
            "body": {"type": "doc", "content": []},
            "body_text": "Party list +1",
            "title": "Party list",
            "guest_name": "Sue",
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(sent) == 1
    assert "Sue" in sent[0]["body"]
    assert sent[0]["data"]["type"] == "guest-edit"


async def test_reminder_set_fire_and_rearm(auth, sent):
    from app.database import get_db
    from app.main import app
    from app.services.notifications import fire_due_reminders

    client, alice, _ = auth
    note = await _make_note(client, alice, title="Water the plants")

    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    resp = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=alice,
        json={"base_version": note["version"], "remind_at": past},
    )
    assert resp.status_code == 200
    assert resp.json()["remind_at"] is not None

    agen = app.dependency_overrides[get_db]()
    db = await anext(agen)
    fired = await fire_due_reminders(db)
    await db.commit()
    assert fired == 1
    assert len(sent) == 1
    assert sent[0]["title"] == "Reminder"
    assert sent[0]["body"] == "Water the plants"

    # Already-fired reminders don't fire again…
    assert await fire_due_reminders(db) == 0
    await agen.aclose()

    # …but setting a new time re-arms it.
    v = resp.json()["version"]
    resp = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=alice,
        json={"base_version": v, "remind_at": past},
    )
    assert resp.status_code == 200
    agen = app.dependency_overrides[get_db]()
    db = await anext(agen)
    assert await fire_due_reminders(db) == 1
    await db.commit()
    await agen.aclose()

    # Clearing the reminder with an explicit null works.
    v = resp.json()["version"]
    resp = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=alice,
        json={"base_version": v, "remind_at": None},
    )
    assert resp.json()["remind_at"] is None

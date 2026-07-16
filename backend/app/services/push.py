"""One notification pipeline, two transports.

`notify_user` resolves a user's registered targets and hands them to
`deliver`, which fans out in the background:

- APNs devices → the self-hosted push-relay (`POST /notify`, X-API-Key),
  which holds the Apple .p8 key for all apps.
- Web Push subscriptions → sent directly to the browser push service,
  signed with a VAPID keypair auto-generated on first use and persisted
  on the media volume (no configuration needed, colloqui-style).

Delivery is strictly best-effort: a dead subscription gets pruned, any
other failure is logged and swallowed — a push must never break a save.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Device, PushSubscription

logger = logging.getLogger(__name__)

_VAPID_CACHE: dict | None = None


def get_vapid() -> dict:
    """{'private_key': pem str, 'public_key': urlsafe-b64 str} — generated
    once and persisted next to the media files."""
    global _VAPID_CACHE
    if _VAPID_CACHE is not None:
        return _VAPID_CACHE
    path = Path(settings.media_root) / "vapid.json"
    if path.exists():
        _VAPID_CACHE = json.loads(path.read_text())
        return _VAPID_CACHE

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from py_vapid import b64urlencode

    key = ec.generate_private_key(ec.SECP256R1())
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    _VAPID_CACHE = {
        "private_key": private_pem,
        "public_key": b64urlencode(public_raw),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_VAPID_CACHE))
    return _VAPID_CACHE


@dataclass
class Targets:
    devices: list[tuple[str, bool]]  # (apns token, sandbox)
    subscriptions: list[dict]  # {endpoint, keys:{p256dh, auth}}


async def targets_for_user(db: AsyncSession, user_id) -> Targets:
    return (await targets_for_users(db, [user_id])).get(user_id, Targets([], []))


async def targets_for_users(db: AsyncSession, user_ids) -> dict:
    """user_id → Targets for a whole participant set in two queries total
    (notifying a shared note's N participants was 2N queries)."""
    ids = list(user_ids)
    out: dict = {uid: Targets(devices=[], subscriptions=[]) for uid in ids}
    if not ids:
        return out
    for d in (
        await db.execute(select(Device).where(Device.user_id.in_(ids)))
    ).scalars():
        out[d.user_id].devices.append((d.token, d.sandbox))
    for s in (
        await db.execute(select(PushSubscription).where(PushSubscription.user_id.in_(ids)))
    ).scalars():
        out[s.user_id].subscriptions.append(
            {"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}}
        )
    return out


async def _send_apns(token: str, sandbox: bool, title: str, body: str, data: dict) -> None:
    if not settings.push_relay_url or not settings.push_relay_api_key:
        return  # relay not configured — web push may still be active
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{settings.push_relay_url.rstrip('/')}/notify",
            headers={"X-API-Key": settings.push_relay_api_key},
            json={
                "bundle_id": settings.apns_bundle_id,
                "device_token": token,
                "title": title,
                "body": body,
                "custom_data": data,
                "sandbox": sandbox,
            },
        )
        resp.raise_for_status()


def _send_webpush_sync(subscription: dict, payload: str) -> None:
    from pywebpush import webpush

    vapid = get_vapid()
    webpush(
        subscription_info=subscription,
        data=payload,
        vapid_private_key=vapid["private_key"],
        vapid_claims={"sub": settings.vapid_subject},
        ttl=3600,
    )


async def _deliver_one_webpush(subscription: dict, payload: str) -> None:
    from pywebpush import WebPushException

    try:
        await asyncio.to_thread(_send_webpush_sync, subscription, payload)
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):
            # Subscription expired/unsubscribed — prune it.
            from app.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(PushSubscription).where(
                        PushSubscription.endpoint == subscription["endpoint"]
                    )
                )
                await db.commit()
        else:
            logger.warning("web push failed (%s): %s", status, exc)


async def _deliver_task(targets: Targets, title: str, body: str, data: dict) -> None:
    payload = json.dumps({"title": title, "body": body, "data": data})
    for token, sandbox in targets.devices:
        try:
            await _send_apns(token, sandbox, title, body, data)
        except Exception as exc:  # noqa: BLE001 — delivery is best-effort
            logger.warning("apns relay send failed: %s", exc)
    for sub in targets.subscriptions:
        await _deliver_one_webpush(sub, payload)


# Keep strong references to in-flight deliveries: asyncio only holds a weak
# reference to tasks, so an unreferenced create_task can be garbage-collected
# mid-flight and the notification silently vanishes.
_pending_deliveries: set[asyncio.Task] = set()


def deliver(targets: Targets, *, title: str, body: str, data: dict | None = None) -> None:
    """Fire-and-forget fan-out. Tests monkeypatch this."""
    if not targets.devices and not targets.subscriptions:
        return
    task = asyncio.create_task(_deliver_task(targets, title, body, data or {}))
    _pending_deliveries.add(task)
    task.add_done_callback(_pending_deliveries.discard)


async def notify_user(
    db: AsyncSession, user_id, *, title: str, body: str, data: dict | None = None
) -> None:
    """Resolve targets with the caller's session, then deliver off-request."""
    targets = await targets_for_user(db, user_id)
    deliver(targets, title=title, body=body, data=data)

"""Push target registration — APNs devices (native app) and Web Push
subscriptions (installed PWA). Both are per-user and idempotent."""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.core.deps import DB, CurrentUser
from app.models import Device, PushSubscription
from app.services.push import get_vapid

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/vapid-public-key")
async def vapid_public_key(user: CurrentUser) -> dict:
    """The applicationServerKey the PWA uses to subscribe."""
    return {"public_key": get_vapid()["public_key"]}


class DeviceIn(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    # Xcode debug builds get sandbox APNs tokens; TestFlight/App Store don't.
    sandbox: bool = False


@router.post("/devices", status_code=204)
async def register_device(payload: DeviceIn, user: CurrentUser, db: DB) -> None:
    existing = (
        await db.execute(select(Device).where(Device.token == payload.token))
    ).scalar_one_or_none()
    if existing is None:
        db.add(Device(user_id=user.id, token=payload.token, sandbox=payload.sandbox))
    else:
        # Token moved to another account (device signed in as someone else).
        existing.user_id = user.id
        existing.sandbox = payload.sandbox


@router.delete("/devices/{token}", status_code=204)
async def unregister_device(token: str, user: CurrentUser, db: DB) -> None:
    await db.execute(
        delete(Device).where(Device.token == token, Device.user_id == user.id)
    )


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class SubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=1024)
    keys: SubscriptionKeys


@router.post("/subscriptions", status_code=204)
async def register_subscription(
    payload: SubscriptionIn, user: CurrentUser, db: DB
) -> None:
    existing = (
        await db.execute(
            select(PushSubscription).where(
                PushSubscription.endpoint == payload.endpoint
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            PushSubscription(
                user_id=user.id,
                endpoint=payload.endpoint,
                p256dh=payload.keys.p256dh,
                auth=payload.keys.auth,
            )
        )
    else:
        existing.user_id = user.id
        existing.p256dh = payload.keys.p256dh
        existing.auth = payload.keys.auth


class SubscriptionDelete(BaseModel):
    endpoint: str = Field(min_length=1, max_length=1024)


@router.post("/subscriptions/delete", status_code=204)
async def unregister_subscription(
    payload: SubscriptionDelete, user: CurrentUser, db: DB
) -> None:
    await db.execute(
        delete(PushSubscription).where(
            PushSubscription.endpoint == payload.endpoint,
            PushSubscription.user_id == user.id,
        )
    )

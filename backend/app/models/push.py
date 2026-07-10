"""Push delivery targets.

Two transports, one pipeline: native iOS devices register APNs tokens
(relayed through the self-hosted push-relay), and installed PWAs register
Web Push subscriptions (sent directly with our VAPID keys). A user may
have any number of each; dead targets are pruned on delivery failure.
"""

import uuid

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import GUID


class Device(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A native app install (APNs token)."""

    __tablename__ = "devices"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    # Debug builds run from Xcode get sandbox APNs tokens; TestFlight/App
    # Store builds get production ones. The client reports which it is.
    sandbox: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PushSubscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A Web Push subscription from an installed PWA."""

    __tablename__ = "push_subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    endpoint: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)
    auth: Mapped[str] = mapped_column(String(100), nullable=False)

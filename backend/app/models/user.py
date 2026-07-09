"""Users and their login sessions."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import GUID


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # Login identifier — short handle, stored lowercase. No email anywhere:
    # this is a self-hosted app with no mail sender; forgotten passwords are
    # an admin action.
    username: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    # First registered user becomes admin; admins will manage users/settings.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # TOTP two-factor: secret is set at setup but 2FA only counts once the
    # user confirms a code (totp_enabled). Locked out with no email? An
    # admin clears these two columns.
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class TotpRecoveryCode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Single-use fallback codes shown once at 2FA enrollment.

    Only SHA-256 hashes are stored; a used code keeps its row (used_at set)
    so the user can see how many remain.
    """

    __tablename__ = "totp_recovery_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Session(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Long-lived opaque bearer session.

    Only the SHA-256 hash of the token is stored — a DB leak never exposes
    a usable credential.
    """

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Coarse device label ("Safari on iPhone") for a future sessions screen.
    device: Mapped[str | None] = mapped_column(String(200), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")

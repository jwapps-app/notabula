"""TOTP two-factor helpers: secrets, QR provisioning, code verification.

The login-time check accepts either a 6-digit authenticator code or one of
the single-use recovery codes issued at enrollment (there is no email in
this system, so recovery codes are the only self-service fallback).
"""

import base64
import io
import secrets
from datetime import datetime, timezone

import pyotp
import qrcode
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import hash_token
from app.models import TotpRecoveryCode, User

RECOVERY_CODE_COUNT = 10


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=username, issuer_name=settings.app_name
    )


def qr_png_base64(uri: str) -> str:
    """QR code for the otpauth:// URI as a base64 PNG (rendered client-side
    as a data URL — no extra frontend dependency)."""
    img = qrcode.make(uri, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def verify_totp_code(secret: str, code: str) -> bool:
    # valid_window=1 tolerates ±30s of clock drift.
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def _normalize(code: str) -> str:
    return code.strip().replace("-", "").replace(" ", "").lower()


async def issue_recovery_codes(db: AsyncSession, user: User) -> list[str]:
    """Replace any existing recovery codes; return the new ones (shown once)."""
    await db.execute(
        delete(TotpRecoveryCode).where(TotpRecoveryCode.user_id == user.id)
    )
    # 8 bytes = 64 bits of entropy per code (hex → 16 chars).
    codes = [secrets.token_hex(8) for _ in range(RECOVERY_CODE_COUNT)]
    for code in codes:
        db.add(TotpRecoveryCode(user_id=user.id, code_hash=hash_token(code)))
    # Display in dash-separated groups for readability; verification strips it.
    return [f"{c[:4]}-{c[4:8]}-{c[8:12]}-{c[12:]}" for c in codes]


async def verify_second_factor(db: AsyncSession, user: User, code: str) -> bool:
    """Accept a current TOTP code or an unused recovery code (consuming it)."""
    normalized = _normalize(code)
    if not normalized:
        return False

    # A 6-digit code is a TOTP; anything else is a recovery code. But a
    # recovery code can be all digits too, so if the TOTP check fails, still
    # fall through to the recovery-code lookup rather than rejecting.
    if len(normalized) == 6 and normalized.isdigit() and user.totp_secret:
        if verify_totp_code(user.totp_secret, normalized):
            return True

    result = await db.execute(
        select(TotpRecoveryCode).where(
            TotpRecoveryCode.user_id == user.id,
            TotpRecoveryCode.code_hash == hash_token(normalized),
            TotpRecoveryCode.used_at.is_(None),
        )
    )
    recovery = result.scalar_one_or_none()
    if recovery is None:
        return False
    recovery.used_at = datetime.now(timezone.utc)
    return True

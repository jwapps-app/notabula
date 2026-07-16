"""Registration, login, logout, current-user.

Sessions are long-lived opaque bearer tokens (only their hash is stored).
The first registered user becomes admin. Each new user gets a default
"Notes" folder, mirroring iOS Notes.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import delete, func, select

from app.config import settings
from app.core.deps import DB, CurrentUser
from app.core.security import (
    generate_token,
    hash_password,
    hash_token,
    password_error,
    verify_password,
)
from app.models import Folder, Session, TotpRecoveryCode, User
from app.schemas.auth import (
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    SessionResult,
    TotpCodeRequest,
    TotpEnableResult,
    TotpSetupResult,
    UserOut,
)
from app.services import totp as totp_service

router = APIRouter(prefix="/auth", tags=["auth"])

_bearer = HTTPBearer(auto_error=False)

# --- Login throttling -----------------------------------------------------
# In-memory backoff keyed on (client IP, username) — process-local state is
# authoritative since we run a single uvicorn worker. 5 failures within the
# window blocks that IP from that account, slowing password/TOTP guessing.
# Keying on the IP (not the username alone) means a third party can't lock a
# victim out of their own account: the victim logging in from a different IP
# is unaffected.
_FAILURE_WINDOW_SECONDS = 300
_FAILURE_LIMIT = 5
_login_failures: dict[tuple[str, str], list[float]] = {}


def _client_ip(request: Request) -> str:
    # nginx sets X-Real-IP to the real remote address (overwriting any
    # client-sent value), so it's trustworthy behind our proxy; direct/dev
    # requests fall back to the socket peer.
    return request.headers.get("x-real-ip") or (
        request.client.host if request.client else "unknown"
    )


def _throttle_check(key: tuple[str, str]) -> None:
    now = time.monotonic()
    attempts = [t for t in _login_failures.get(key, []) if now - t < _FAILURE_WINDOW_SECONDS]
    _login_failures[key] = attempts
    if len(attempts) >= _FAILURE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts — wait a few minutes and try again",
        )


def _throttle_fail(key: tuple[str, str]) -> None:
    now = time.monotonic()
    _login_failures.setdefault(key, []).append(now)
    # Bound the dict: one-off (ip, username) keys were never pruned, so the
    # map grew for the process's lifetime. Sweep expired keys occasionally.
    if len(_login_failures) > 1000:
        for k in [
            k
            for k, ts in _login_failures.items()
            if not ts or now - ts[-1] >= _FAILURE_WINDOW_SECONDS
        ]:
            _login_failures.pop(k, None)


def _throttle_clear(key: tuple[str, str]) -> None:
    _login_failures.pop(key, None)


async def _create_session(db, user: User) -> str:
    token = generate_token()
    db.add(
        Session(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.session_ttl_days),
        )
    )
    return token


@router.post("/register", response_model=SessionResult, status_code=201)
async def register(payload: RegisterRequest, db: DB) -> SessionResult:
    """First-run bootstrap only: creates the admin account, then the door
    closes for good. All later accounts are created by an admin."""
    user_count = (await db.execute(select(func.count(User.id)))).scalar_one()
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is closed — ask your admin for an account",
        )
    if err := password_error(payload.password, settings.min_password_length):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=err)

    user = User(
        username=payload.username,
        name=payload.name.strip(),
        password_hash=hash_password(payload.password),
        is_admin=True,  # the bootstrap user administers the instance
    )
    db.add(user)
    await db.flush()

    # Every account starts with the undeletable default folder.
    db.add(Folder(owner_id=user.id, name="Notes", is_default=True, position=0))

    token = await _create_session(db, user)
    return SessionResult(session_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=SessionResult)
async def login(payload: LoginRequest, request: Request, db: DB) -> SessionResult:
    result = await db.execute(
        select(User).where(User.username == payload.username.strip().lower())
    )
    user = result.scalar_one_or_none()
    username = payload.username.strip().lower()
    key = (_client_ip(request), username)
    _throttle_check(key)
    if user is None or not verify_password(payload.password, user.password_hash):
        _throttle_fail(key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    if user.totp_enabled:
        # Second factor: authenticator code or single-use recovery code.
        # "totp_required" is a machine-readable sentinel the client watches
        # for to reveal the code field and retry. (Not a failed *attempt*.)
        if not payload.totp_code:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="totp_required"
            )
        if not await totp_service.verify_second_factor(db, user, payload.totp_code):
            _throttle_fail(key)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid verification code",
            )

    _throttle_clear(key)
    token = await _create_session(db, user)
    return SessionResult(session_token=token, user=UserOut.model_validate(user))


@router.post("/logout", status_code=204)
async def logout(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: DB,
) -> None:
    if credentials is not None:
        result = await db.execute(
            select(Session).where(Session.token_hash == hash_token(credentials.credentials))
        )
        session = result.scalar_one_or_none()
        if session is not None:
            await db.delete(session)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


# --- Capture token (revocable, capture-only credential) ------------------


@router.get("/capture-token")
async def capture_token_status(user: CurrentUser) -> dict:
    """Whether a capture token exists — the plaintext is only ever shown
    once, at mint time."""
    return {"exists": user.capture_token_hash is not None}


@router.post("/capture-token")
async def mint_capture_token(user: CurrentUser, db: DB) -> dict:
    """Mint (or replace) this account's capture token. Returned in
    plaintext once; only its hash is stored. It authorizes the capture
    endpoint and nothing else, and can be revoked without touching the
    account password or sessions."""
    token = generate_token()
    user.capture_token_hash = hash_token(token)
    return {"token": token}


@router.delete("/capture-token", status_code=204)
async def revoke_capture_token(user: CurrentUser, db: DB) -> None:
    user.capture_token_hash = None


class PasswordVerifyRequest(BaseModel):
    password: str


@router.post("/verify-password", status_code=204)
async def verify_password_check(
    payload: PasswordVerifyRequest, request: Request, user: CurrentUser
) -> None:
    """Confirm the caller's account password (used before locking a note —
    the same password becomes the encryption passphrase client-side).
    Shares the login throttle so it can't be used as a guessing oracle."""
    key = (_client_ip(request), user.username)
    _throttle_check(key)
    if not verify_password(payload.password, user.password_hash):
        _throttle_fail(key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
        )
    _throttle_clear(key)


@router.post("/password", status_code=204)
async def change_password(
    payload: PasswordChangeRequest,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    user: CurrentUser,
    db: DB,
) -> None:
    """Change my own password. Signs out every other device (but not this
    one) — standard hygiene in case the change is because of a leak."""
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )
    if err := password_error(payload.new_password, settings.min_password_length):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=err)
    user.password_hash = hash_password(payload.new_password)
    current_hash = hash_token(credentials.credentials) if credentials else ""
    await db.execute(
        delete(Session).where(
            Session.user_id == user.id, Session.token_hash != current_hash
        )
    )


class UserSummary(BaseModel):
    username: str
    name: str


@router.get("/users", response_model=list[UserSummary])
async def list_usernames(user: CurrentUser, db: DB) -> list[UserSummary]:
    """Everyone on this server except me — the share-dialog picker.

    A self-hosted instance is one household/team, so members may see each
    other's usernames (they're share targets, like iOS contacts).
    """
    result = await db.execute(
        select(User.username, User.name).where(User.id != user.id).order_by(User.name)
    )
    return [UserSummary(username=u, name=n) for u, n in result.all()]


# --- TOTP two-factor enrollment -----------------------------------------


@router.post("/totp/setup", response_model=TotpSetupResult)
async def totp_setup(user: CurrentUser, db: DB) -> TotpSetupResult:
    """Generate a (pending) secret and QR code. 2FA is not active until the
    user proves the authenticator works via /totp/enable."""
    if user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Two-factor is already enabled",
        )
    secret = totp_service.generate_secret()
    user.totp_secret = secret
    await db.flush()
    uri = totp_service.provisioning_uri(secret, user.username)
    return TotpSetupResult(
        secret=secret,
        otpauth_uri=uri,
        qr_png_base64=totp_service.qr_png_base64(uri),
    )


@router.post("/totp/enable", response_model=TotpEnableResult)
async def totp_enable(
    payload: TotpCodeRequest, user: CurrentUser, db: DB
) -> TotpEnableResult:
    if user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Two-factor is already enabled",
        )
    if not user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Run setup first",
        )
    if not totp_service.verify_totp_code(user.totp_secret, payload.code.strip()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid verification code",
        )
    user.totp_enabled = True
    codes = await totp_service.issue_recovery_codes(db, user)
    return TotpEnableResult(recovery_codes=codes)


@router.post("/totp/disable", status_code=204)
async def totp_disable(payload: TotpCodeRequest, user: CurrentUser, db: DB) -> None:
    """Turning 2FA off requires proving the second factor one last time."""
    if not user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Two-factor is not enabled",
        )
    if not await totp_service.verify_second_factor(db, user, payload.code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid verification code",
        )
    user.totp_enabled = False
    user.totp_secret = None
    await db.execute(
        delete(TotpRecoveryCode).where(TotpRecoveryCode.user_id == user.id)
    )

"""Security primitives: password hashing and session-token handling.

Passwords are hashed with bcrypt. Session tokens are random secrets; we
persist only their SHA-256 hashes so a database leak never exposes a
usable token.
"""

import hashlib
import secrets

import bcrypt

# bcrypt silently ignores everything past 72 bytes — reject instead, so users
# aren't misled into thinking their whole passphrase counts.
MAX_PASSWORD_BYTES = 72


def password_error(password: str, min_length: int) -> str | None:
    """Human-readable policy violation, or None if the password is acceptable."""
    if len(password) < min_length:
        return f"Password must be at least {min_length} characters"
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return f"Password must be at most {MAX_PASSWORD_BYTES} bytes"
    return None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def generate_token(nbytes: int = 32) -> str:
    """Return a URL-safe random secret."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Deterministic hash for storage/lookup of a bearer token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

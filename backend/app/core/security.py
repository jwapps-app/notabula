"""Security primitives: password hashing and session-token handling.

Passwords are hashed with bcrypt. Session tokens are random secrets; we
persist only their SHA-256 hashes so a database leak never exposes a
usable token.
"""

import hashlib
import secrets

import bcrypt


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

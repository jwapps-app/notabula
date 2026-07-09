"""Auth request/response schemas."""

import re
import uuid

from pydantic import BaseModel, Field, field_validator

# 3–32 chars; letters/digits plus . _ - ; must start with a letter or digit.
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")


class RegisterRequest(BaseModel):
    username: str
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=200)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, v: str) -> str:
        v = v.strip().lower()
        if not USERNAME_RE.fullmatch(v):
            raise ValueError(
                "Username must be 3–32 characters: letters, numbers, . _ -"
            )
        return v


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    username: str
    password: str
    # Required (as a second request) when the account has 2FA enabled.
    # Accepts a 6-digit authenticator code or a recovery code.
    totp_code: str | None = None


class UserOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    username: str
    name: str
    is_admin: bool
    totp_enabled: bool = False


class SessionResult(BaseModel):
    session_token: str
    user: UserOut


class TotpSetupResult(BaseModel):
    secret: str
    otpauth_uri: str
    qr_png_base64: str


class TotpCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=20)


class TotpEnableResult(BaseModel):
    recovery_codes: list[str]

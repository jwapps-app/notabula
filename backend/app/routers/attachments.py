"""Attachment upload — images embedded into note bodies.

Files are streamed to MEDIA_ROOT/attachments under an unguessable UUID
name and served back at /media/attachments/<name> (nginx in compose, the
API's /media mount otherwise).
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, status

from app.config import settings
from app.core.deps import DB, CurrentUser
from app.models import Attachment

router = APIRouter(prefix="/attachments", tags=["attachments"])

ALLOWED_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/svg+xml": ".svg",
}

CHUNK = 1024 * 1024


@router.post("", status_code=201)
async def upload_attachment(file: UploadFile, user: CurrentUser, db: DB) -> dict:
    ext = ALLOWED_TYPES.get((file.content_type or "").lower())
    if ext is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only image uploads are supported",
        )

    max_bytes = settings.max_upload_mb * 1024 * 1024
    stored_name = f"{uuid.uuid4().hex}{ext}"
    directory = Path(settings.media_root) / "attachments"

    size = 0
    try:
        directory.mkdir(parents=True, exist_ok=True)
        dest = directory / stored_name
        with dest.open("wb") as out:
            while chunk := await file.read(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File exceeds {settings.max_upload_mb} MB limit",
                    )
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except OSError as exc:
        # Storage trouble (permissions, disk full, read-only volume…) —
        # name the cause instead of a bare 500 so it's fixable without
        # digging through container logs.
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail=f"Server cannot write to media storage: {exc}",
        )

    attachment = Attachment(
        owner_id=user.id,
        stored_name=stored_name,
        original_name=file.filename or stored_name,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=size,
    )
    db.add(attachment)
    await db.flush()

    return {
        "id": str(attachment.id),
        "url": f"/media/attachments/{stored_name}",
        "content_type": attachment.content_type,
        "size_bytes": size,
    }

"""Link-preview (unfurl) endpoint.

Authenticated so it isn't an open SSRF proxy for anonymous callers; the
result is cached per URL (shared across users), refreshed after a TTL.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import DB, CurrentUser
from app.models import LinkPreview
from app.services.unfurl import fetch_preview

router = APIRouter(prefix="/links", tags=["links"])

REFRESH_AFTER = timedelta(days=7)


class LinkPreviewOut(BaseModel):
    url: str
    title: str | None
    description: str | None
    image_url: str | None
    site_name: str | None
    ok: bool


def _out(row: LinkPreview) -> LinkPreviewOut:
    return LinkPreviewOut(
        url=row.url,
        title=row.title,
        description=row.description,
        image_url=row.image_url,
        site_name=row.site_name,
        ok=row.ok,
    )


@router.get("/preview", response_model=LinkPreviewOut)
async def preview(
    user: CurrentUser,
    db: DB,
    url: str = Query(max_length=2048),
) -> LinkPreviewOut:
    url = url.strip()
    if urlparse(url).scheme not in ("http", "https"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid URL"
        )

    row = (
        await db.execute(select(LinkPreview).where(LinkPreview.url == url))
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row and row.fetched_at.replace(tzinfo=timezone.utc) > now - REFRESH_AFTER:
        return _out(row)

    data = await fetch_preview(url)
    if row is None:
        row = LinkPreview(url=url)
        db.add(row)
    row.title = (data or {}).get("title")
    row.description = (data or {}).get("description")
    row.image_url = (data or {}).get("image_url")
    row.site_name = (data or {}).get("site_name")
    row.ok = data is not None
    row.fetched_at = now
    await db.flush()
    return _out(row)

"""Link-preview (unfurl) endpoint.

Authenticated so it isn't an open SSRF proxy for anonymous callers; the
result is cached per URL (shared across users), refreshed after a TTL.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

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

    # Uniqueness (and the only index) is the md5(url) expression index from
    # migration 0016 — a btree on the raw 2048-char column can exceed the
    # row limit — so look up through md5 too, or the query seq-scans the
    # cache. SQLite (tests) has no md5(): plain equality there.
    if db.get_bind().dialect.name == "postgresql":
        match = func.md5(LinkPreview.url) == func.md5(url)
    else:
        match = LinkPreview.url == url
    row = (await db.execute(select(LinkPreview).where(match))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row and row.fetched_at.replace(tzinfo=timezone.utc) > now - REFRESH_AFTER:
        return _out(row)

    # Release the DB connection back to the pool during the (up to ~6s)
    # external unfurl — don't pin a pooled connection on a network wait.
    # Safe: expire_on_commit=False, and nothing is pending yet.
    await db.commit()
    data = await fetch_preview(url)
    fields = {
        "title": (data or {}).get("title"),
        "description": (data or {}).get("description"),
        "image_url": (data or {}).get("image_url"),
        "site_name": (data or {}).get("site_name"),
        "ok": data is not None,
        "fetched_at": now,
    }
    if row is None:
        row = LinkPreview(url=url, **fields)
        try:
            async with db.begin_nested():
                db.add(row)
                await db.flush()
        except IntegrityError:
            # Lost a race with a concurrent first preview of this URL — the
            # unique index caught it; serve the row that won.
            row = (await db.execute(select(LinkPreview).where(match))).scalar_one()
        return _out(row)
    for name, value in fields.items():
        setattr(row, name, value)
    await db.flush()
    return _out(row)

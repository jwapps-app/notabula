"""API router aggregation — feature routers mount under /api/v1."""

from fastapi import APIRouter

from app.routers import (
    admin,
    attachments,
    auth,
    folders,
    links,
    meta,
    notes,
    public,
    push,
    search,
    shares,
    tags,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(meta.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(attachments.router)
api_router.include_router(folders.router)
api_router.include_router(links.router)
api_router.include_router(notes.router)
api_router.include_router(public.router)
api_router.include_router(push.router)
api_router.include_router(search.router)
api_router.include_router(shares.router)
api_router.include_router(tags.router)

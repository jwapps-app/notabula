"""Public instance metadata — branding and capabilities for clients."""

from fastapi import APIRouter
from sqlalchemy import func, select

from app.config import settings
from app.core.deps import DB
from app.models import User

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("")
async def get_meta(db: DB) -> dict:
    user_count = (await db.execute(select(func.count(User.id)))).scalar_one()
    return {
        "app_name": settings.app_name,
        "app_tagline": settings.app_tagline,
        "support_email": settings.support_email,
        # True only during first-run bootstrap: the register page exists to
        # create the admin account, then disappears.
        "allow_registration": user_count == 0,
    }

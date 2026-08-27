"""Internal JSON API routes.

All endpoints under /api are authenticated (require active session) and
return JSON.  No HTML templates are rendered here.
"""
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.repositories.property_repo import property_repo

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/properties/search")
async def search_properties(
    q: str = "",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Search active properties by external_id or title fragment.

    Returns at most 6 results as a JSON array.  Requires at least 2
    non-whitespace characters to prevent full-table scans.
    Gracefully returns an empty list on any DB error (typeahead degradation).
    """
    if len(q.strip()) < 2:
        return JSONResponse([])
    try:
        results = await property_repo.search(db, q.strip())
        return JSONResponse(results)
    except Exception:
        logger.exception("property search failed q=%r", q)
        return JSONResponse([], status_code=200)

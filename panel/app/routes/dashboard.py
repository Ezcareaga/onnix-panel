import logging

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.dependencies import require_admin
from app.services.dashboard_service import dashboard_service
from app.models.user import User
from app.tz import get_templates

logger = logging.getLogger(__name__)

router = APIRouter()
templates = get_templates()

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: User = Depends(require_admin),
                    db: AsyncSession = Depends(get_db)):
    logger.info("Dashboard accessed: user=%s", user.email)
    stats = await dashboard_service.get_stats(db)
    context = {"request": request, "user": user, "stats": stats}

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/dashboard_stats.html", context)

    return templates.TemplateResponse("dashboard.html", context)

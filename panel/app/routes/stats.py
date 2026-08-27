import logging

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.dependencies import require_admin
from app.services.dashboard_service import dashboard_service
from app.services.stats_service import stats_service
from app.models.user import User
from app.tz import get_templates

logger = logging.getLogger(__name__)

router = APIRouter()
templates = get_templates()

# Períodos válidos del selector de análisis (whitelist estricta)
_DEMAND_DAYS = (30, 90, 365)


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request, days: int = 30, user: User = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    logger.info("Stats accessed: user=%s days=%d", user.email, days)
    if days not in _DEMAND_DAYS:
        days = 30
    # Demanda + gap usan la ventana completa del selector. Las series
    # diarias legacy (mensajes/errores por día) se capean a 90d: 365 filas
    # de barras no son legibles ni útiles.
    stats = await stats_service.get_stats(db, days=min(days, 90))
    demand = await dashboard_service.get_demand_stats(db, days=days)
    gap = await stats_service.get_gap_analysis(db, days=days)
    context = {
        "request": request, "user": user, "stats": stats,
        "demand": demand, "gap": gap, "days": days,
    }

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/stats_counters.html", context)

    return templates.TemplateResponse("stats.html", context)


@router.get("/stats/ai")
async def ai_metrics_redirect():
    """Redirect legacy URL to the unified bot health page (Detalle tecnico tab)."""
    return RedirectResponse("/stats/health?tab=detalle", status_code=301)

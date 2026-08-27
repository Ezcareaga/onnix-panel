import logging

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
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
    # Las series diarias (mensajes/errores por día) se capean a 90d: 365 filas
    # de barras no son legibles ni útiles.
    stats = await stats_service.get_stats(db, days=min(days, 90))
    context = {
        "request": request, "user": user, "stats": stats,
        "days": days,
    }

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/stats_counters.html", context)

    return templates.TemplateResponse("stats.html", context)


# `/stats/ai` redirigía a `/stats/health`, y las dos se fueron con el bot:
# medían el costo y la latencia de las llamadas al LLM. Ya no hay ninguna.

"""Route for GET /stats/health — unified bot health + AI metrics dashboard.

Admin-only. Returns full page or partial based on HX-Request header.
Auto-refreshes every 30 s via HTMX polling on the client.
Tab switching (resumen / detalle) is handled client-side by Alpine.js.
"""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin
from app.models.user import User
from app.services import ai_metrics_service
from app.services.metrics_service import MetricsService
from app.tz import get_templates

logger = logging.getLogger(__name__)

router = APIRouter()
templates = get_templates()


@router.get("/stats/health", response_class=HTMLResponse)
async def bot_health(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
    tab: str = "resumen",
    days: int = 7,
) -> HTMLResponse:
    """Unified bot health dashboard — full page or HTMX partial."""
    if days not in (7, 30, 90):
        days = 7
    active_tab = tab if tab in ("resumen", "detalle") else "resumen"

    snapshot = await MetricsService(db).get_bot_health()

    # Tab 2 data loaded on every request — Alpine switches client-side
    tokens_by_day = await ai_metrics_service.get_last_7_days_tokens_by_day(db, days=days)
    avg_latency = await ai_metrics_service.get_avg_latency_ms(db, days=days)
    cost_estimate = await ai_metrics_service.get_cost_estimate_usd(db, days=days)
    total_tokens_in = sum(r["tokens_in"] for r in tokens_by_day)
    total_tokens_out = sum(r["tokens_out"] for r in tokens_by_day)
    total_messages = sum(r["messages"] for r in tokens_by_day)

    logger.info(
        "bot_health: user=%s tab=%s days=%d", user.email, active_tab, days
    )

    ctx = {
        "request": request,
        "user": user,
        "snapshot": snapshot,
        "active_tab": active_tab,
        "days": days,
        "tokens_by_day": tokens_by_day,
        "avg_latency_ms": avg_latency,
        "cost_estimate": cost_estimate,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "total_messages": total_messages,
    }

    template = (
        "partials/bot_health_tabs_inner.html"
        if request.headers.get("HX-Request")
        else "bot_health.html"
    )
    return templates.TemplateResponse(template, ctx)

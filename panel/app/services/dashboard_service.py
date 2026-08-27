import logging
from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.contact_repo import contact_repo
from app.repositories.bot_setting_repo import bot_setting_repo
from app.repositories.conversation_repo import conversation_repo
from app.repositories.lead_repo import lead_repo
from app.repositories.message_repo import message_repo
from app.repositories.bot_error_repo import bot_error_repo
from app.services.lead_service import lead_service
from app.tz import PYT

logger = logging.getLogger(__name__)


# Meses abreviados ES — labels de la serie mensual (sparkline)
_MONTH_LABELS_ES = (
    "Ene", "Feb", "Mar", "Abr", "May", "Jun",
    "Jul", "Ago", "Sep", "Oct", "Nov", "Dic",
)


def _top_items(
    key_counts: Counter, key_labels: dict[str, Counter], limit: int = 5,
) -> list[dict]:
    """Top-N como [{label, count, pct}] — pct relativo al máximo (width de barra).

    El label visible es el spelling original más frecuente del grupo
    (agrupado por clave unaccent), p.ej. 'Asunción' gana a 'Asuncion'.
    """
    top = key_counts.most_common(limit)
    if not top:
        return []
    max_count = top[0][1]
    return [
        {
            "label": key_labels[key].most_common(1)[0][0],
            "count": count,
            "pct": round(count / max_count * 100),
        }
        for key, count in top
    ]


class DashboardService:
    @staticmethod
    async def get_stats(db: AsyncSession) -> dict:
        status_counts = await contact_repo.count_by_status(db)
        bot_enabled_raw = await bot_setting_repo.get_value(db, "bot_enabled")
        new_today = await contact_repo.count_today(db)

        messages_24h = await message_repo.count_recent(db, hours=24)
        errors_24h = await bot_error_repo.count_recent(db, hours=24)

        # DASH-01 — counters de la cola de trabajo, MISMA fuente que los
        # badges de /leads (count_leads_per_tab) para que los numeros del
        # dashboard coincidan con los tabs. Universo distinto a
        # status_counts (que es global e incluye asignados/import:excel).
        lead_tab_counts = await lead_service.count_leads_per_tab(db)

        logger.info("Dashboard stats loaded: total=%s, new_today=%s, msgs_24h=%s", sum(status_counts.values()), new_today, messages_24h)
        return {
            "status_counts": status_counts,
            "total_leads": sum(status_counts.values()),
            "bot_enabled": bot_enabled_raw == "true" if bot_enabled_raw else False,
            "new_today": new_today,
            "messages_24h": messages_24h,
            "errors_24h": errors_24h,
            "lead_tab_counts": lead_tab_counts,
        }

    # `get_demand_stats` y `get_demand_monthly_series` se fueron con el vertical
    # inmobiliario. Contaban "consultas" de tres fuentes que ya no existen: un
    # lead de InfoCasas, un lead del bot sobre una propiedad, y una búsqueda del
    # bot con filtros. Las tres eran del catálogo, y agrupaban por ciudad y por
    # tipo de propiedad.
    #
    # Si el panel vuelve a querer un "qué consulta la gente", va a ser otra cosa:
    # sobre los mensajes de los tres canales, no sobre un catálogo.


dashboard_service = DashboardService()

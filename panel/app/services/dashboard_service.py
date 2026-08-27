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

# Fuentes canónicas de demanda — siempre presentes en by_source (aunque en 0)
_DEMAND_SOURCES = ("infocasas", "whatsapp", "telegram")

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

    @staticmethod
    async def get_demand_stats(db: AsyncSession, days: int = 30) -> dict:
        """Mini-análisis de demanda — qué consulta la gente (ventana de N días).

        Una "consulta" es:
        - un lead InfoCasas (contacts JOIN infocasas_properties), o
        - un lead del bot sobre una propiedad (contacts.property_id), o
        - una búsqueda al bot con filtros (conversations.search_context.filtros).

        Los repos agrupan con unaccent (regla 7); acá solo se cuenta.
        Caveat documentado: un mismo usuario puede aportar una búsqueda
        (filtros) y un lead — son eventos de demanda distintos y se
        cuentan como tales.
        """
        rows = await lead_repo.get_demand_rows(db, days=days)
        rows += await conversation_repo.get_demand_filter_rows(db, days=days)

        by_source: dict[str, int] = {src: 0 for src in _DEMAND_SOURCES}
        city_counts: Counter = Counter()
        type_counts: Counter = Counter()
        city_labels: dict[str, Counter] = defaultdict(Counter)
        type_labels: dict[str, Counter] = defaultdict(Counter)
        operations = {"venta": 0, "alquiler": 0}
        sin_ciudad = 0

        for row in rows:
            source = row["source"] if row["source"] in by_source else "whatsapp"
            by_source[source] += 1
            if row["city_key"]:
                city_counts[row["city_key"]] += 1
                city_labels[row["city_key"]][row["city"].strip()] += 1
            else:
                sin_ciudad += 1
            if row["ptype_key"]:
                type_counts[row["ptype_key"]] += 1
                type_labels[row["ptype_key"]][row["ptype"].strip()] += 1
            if row["operation"] in operations:
                operations[row["operation"]] += 1

        demand = {
            "days": days,
            "total": len(rows),
            "by_source": by_source,
            "top_cities": _top_items(city_counts, city_labels),
            "top_types": _top_items(type_counts, type_labels),
            "operations": operations,
            "sin_ciudad": sin_ciudad,
            "monthly": await DashboardService.get_demand_monthly_series(db),
        }
        logger.info(
            "Demand stats loaded: days=%s total=%s by_source=%s",
            days, demand["total"], by_source,
        )
        return demand

    @staticmethod
    async def get_demand_monthly_series(
        db: AsyncSession, months: int = 6,
    ) -> list[dict]:
        """Serie mensual de demanda — [{label, count, pct}] cronológica.

        Misma definición de "consulta" que get_demand_stats (leads IC +
        leads bot con property_id + búsquedas con filtros), agrupada por
        mes calendario PARAGUAYO. Siempre devuelve ``months`` buckets — meses
        sin datos van en 0. ``pct`` es relativo al máximo de la serie
        (alimenta la altura del sparkline; 0 si la serie está vacía).
        """
        rows = await lead_repo.get_demand_monthly_counts(db, months=months)
        rows += await conversation_repo.get_demand_filter_monthly_counts(
            db, months=months,
        )
        counts: Counter = Counter()
        for row in rows:
            counts[(row["month"].year, row["month"].month)] += row["n"]

        # El mes en curso es el paraguayo, igual que los buckets del repo:
        # las tres primeras horas UTC de cada 1° de mes son todavía el mes
        # anterior acá, y la serie encabezaba un mes que no había empezado.
        now = datetime.now(PYT)
        keys: list[tuple[int, int]] = []
        year, month = now.year, now.month
        for _ in range(months):
            keys.append((year, month))
            month -= 1
            if month == 0:
                month, year = 12, year - 1
        keys.reverse()

        max_count = max((counts[key] for key in keys), default=0)
        series = [
            {
                "label": _MONTH_LABELS_ES[m - 1],
                "count": counts[(y, m)],
                "pct": round(counts[(y, m)] / max_count * 100)
                if max_count > 0 else 0,
            }
            for y, m in keys
        ]
        logger.info(
            "Demand monthly series loaded: months=%s total=%s",
            months, sum(item["count"] for item in series),
        )
        return series


dashboard_service = DashboardService()

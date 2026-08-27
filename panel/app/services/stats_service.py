import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.contact_repo import contact_repo
from app.repositories.conversation_repo import conversation_repo
from app.repositories.lead_event_repo import lead_event_repo
from app.repositories.lead_repo import lead_repo
from app.repositories.message_repo import message_repo
from app.repositories.bot_error_repo import bot_error_repo
from app.tz import PYT

logger = logging.getLogger(__name__)

def _a_fecha(valor) -> date | None:
    """La 'day' de cada repo llega como date o como texto, segun el driver."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _serie_por_dia(filas, days: int) -> list[dict]:
    """Devuelve exactamente ``days`` dias, del mas viejo al mas nuevo, con los
    dias sin actividad en cero.

    Un ``GROUP BY DATE(created_at)`` solo devuelve los dias que tuvieron algo.
    La barra mas larga se dibujaba pegada a la anterior sin que nada dijera que
    entre las dos habia tres dias en cero: el grafico mentia sobre el ritmo,
    que es lo unico que un grafico de barras por dia sirve para mostrar.

    El patron es el mismo que ``metrics_repository.ai_cost_by_day_last_7d``:
    diccionario por fecha y despues un rango completo. La ventana es la del
    dia PARAGUAYO, igual que la de ese metodo y que la de los repos que
    alimentan esto: si el relleno usara el hoy UTC, entre las 21:00 y las
    23:59 locales el rango arrancaria un dia tarde y se comeria el bucket
    mas viejo para agregar un dia futuro vacio.

    Acepta las dos formas que devuelven los repos: tuplas ``(day, count)`` y
    diccionarios ``{"day": ..., "count": ...}``.
    """
    por_dia: dict[date, int] = {}
    for fila in filas or ():
        if isinstance(fila, dict):
            dia, cuenta = fila.get("day"), fila.get("count", 0)
        else:
            dia, cuenta = fila[0], fila[1]
        dia = _a_fecha(dia)
        if dia is not None:
            por_dia[dia] = por_dia.get(dia, 0) + int(cuenta or 0)

    hoy = datetime.now(PYT).date()
    arranque = hoy - timedelta(days=days - 1)
    return [
        {"day": str(arranque + timedelta(days=n)), "count": por_dia.get(arranque + timedelta(days=n), 0)}
        for n in range(days)
    ]


class StatsService:
    @staticmethod
    async def get_stats(db: AsyncSession, days: int = 7) -> dict:
        leads_by_source = await contact_repo.count_by_source(db)
        weekly = await contact_repo.weekly_evolution(db, days=days)
        events_this_week = await lead_event_repo.count_by_type_this_week(db)
        new_today = await contact_repo.count_today(db)

        messages_per_day = await message_repo.count_per_day(db, days=days)
        errors_per_day = await bot_error_repo.count_per_day(db, days=days)

        # Tasa de conversion (InfoCasas contacts)
        status_counts = await contact_repo.count_by_status_for_source(db, "infocasas")
        total_ic = sum(status_counts.values())
        # M6.2 (OQ-5): visit_scheduled reintroducido como flag de conversion.
        # Per ROADMAP §M6.2: contact con visita agendada cuenta como converted.
        converted = sum(status_counts.get(s, 0) for s in ('interested', 'visit_scheduled', 'closed'))
        conversion_rate = round(converted / total_ic * 100, 1) if total_ic > 0 else 0

        logger.info("Stats loaded: days=%s, conversion_rate=%s%%", days, conversion_rate)
        return {
            "leads_by_source": leads_by_source,
            "weekly_evolution": _serie_por_dia(weekly, days),
            "events_this_week": events_this_week,
            "new_today": new_today,
            "days": days,
            "messages_per_day": _serie_por_dia(messages_per_day, days),
            "errors_per_day": _serie_por_dia(errors_per_day, days),
            "conversion_rate": conversion_rate,
            "conversion_total": total_ic,
            "conversion_converted": converted,
        }

    # `get_gap_analysis` se fue con el vertical inmobiliario: cruzaba la
    # demanda de los leads contra el STOCK activo de propiedades por
    # ciudad+tipo para decir dónde salir a captar. Sin catálogo no hay stock
    # contra qué cruzar, y la mitad del cálculo era `property_repo`.


stats_service = StatsService()

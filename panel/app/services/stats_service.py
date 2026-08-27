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
from app.repositories.property_repo import StockCombo, property_repo
from app.tz import PYT

logger = logging.getLogger(__name__)

# El repo promete devolver todos los pares pedidos; el default es por si esa
# promesa se rompe, para que la pantalla diga 0 en vez de tirar KeyError.
_SIN_STOCK = StockCombo(0, None)


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

    @staticmethod
    async def get_gap_analysis(
        db: AsyncSession, days: int = 30, limit: int = 8,
    ) -> dict:
        """Gap oferta/demanda — qué piden vs qué tenemos.

        Demanda: MISMO universo que get_demand_stats del dashboard (leads
        InfoCasas + leads bot con property_id + búsquedas con filtros),
        reusando los mismos repos. Se agrupa por combo (city_key,
        ptype_key) — solo filas con ciudad Y tipo informados cuentan como
        combo (honestidad del dato: lo demás no es accionable para captar).

        Oferta: stock ACTIVO matching por combo via
        property_repo.count_active_by_city_type (ciudad exacta, tipo con
        match parcial bidireccional — ver docstring del repo).

        Señal 'captar': stock < demanda/2 (menos de media propiedad por
        cada dos consultas → vale salir a captar en esa combinación).

        Cada fila trae ademas `ptype_slug`: el `properties.property_type` con
        el que se conto ese stock, que es lo que el link a `/properties` tiene
        que mandar. Sin stock viene en None y el link se queda con la ciudad.
        """
        rows = await lead_repo.get_demand_rows(db, days=days)
        rows += await conversation_repo.get_demand_filter_rows(db, days=days)

        combo_counts: Counter = Counter()
        city_labels: dict[tuple, Counter] = defaultdict(Counter)
        ptype_labels: dict[tuple, Counter] = defaultdict(Counter)
        for row in rows:
            if not (row["city_key"] and row["ptype_key"]):
                continue
            key = (row["city_key"], row["ptype_key"])
            combo_counts[key] += 1
            city_labels[key][row["city"].strip()] += 1
            ptype_labels[key][row["ptype"].strip()] += 1

        top = combo_counts.most_common(limit)
        stock = await property_repo.count_active_by_city_type(
            db, [key for key, _ in top],
        )
        # NB: la clave del dict de salida es "rows" (no "items") a propósito
        # — en Jinja `gap.items` resuelve al método dict.items, no a la clave.
        gap = {
            "days": days,
            "total_combos": len(combo_counts),
            "rows": [
                {
                    "city": city_labels[key].most_common(1)[0][0],
                    "ptype": ptype_labels[key].most_common(1)[0][0],
                    "demand": count,
                    "stock": stock.get(key, _SIN_STOCK).stock,
                    "captar": stock.get(key, _SIN_STOCK).stock < count / 2,
                    # El slug con el que se conto el stock, para que el link a
                    # /properties filtre por lo mismo: ese listado usa
                    # `property_type = :valor` exacto y la etiqueta de la
                    # demanda no siempre lo es ('duplex' vs 'casa-duplex').
                    "ptype_slug": stock.get(key, _SIN_STOCK).slug,
                }
                for key, count in top
            ],
        }
        logger.info(
            "Gap analysis loaded: days=%s combos=%s shown=%s captar=%s",
            days, gap["total_combos"], len(gap["rows"]),
            sum(1 for r in gap["rows"] if r["captar"]),
        )
        return gap


stats_service = StatsService()

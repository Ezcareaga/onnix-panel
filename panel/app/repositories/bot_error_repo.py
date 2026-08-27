from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bot_error import BotError
from sqlalchemy import literal_column

from app.tz import PYT_SQL_ZONE, pyt_day_start

# El mismo truco que PYT_SQL_ZONE en app/tz.py: literal y no bind param, porque
# timezone() esta sobrecargada y con un parametro sin tipo la resolucion queda
# a merced de las reglas de categoria.
_UTC_SQL_ZONE = literal_column("'UTC'")


class BotErrorRepository:
    @staticmethod
    async def count_recent(db: AsyncSession, hours: int = 24) -> int:
        """Count bot errors in the last N hours."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        result = await db.execute(
            select(func.count()).select_from(BotError).where(
                BotError.created_at > cutoff
            )
        )
        return result.scalar() or 0

    @staticmethod
    async def count_per_day(
        db: AsyncSession, days: int = 7
    ) -> list[dict]:
        """Count bot errors per day for last N days.

        Returns list of {"day": "YYYY-MM-DD", "count": int} sorted ascending.
        Days with no errors NO aparecen: los rellena StatsService.

        Mismo corte que MessageRepository.count_per_day — arranque del dia
        PARAGUAYO de hace ``days - 1`` dias, asi son exactamente ``days`` dias.
        """
        # OJO: bot_errors.created_at es la unica de estas columnas que quedo
        # `timestamp WITHOUT time zone`. El modelo declara DateTime(timezone=True)
        # y miente: el schema real es naive. Lo que guarda es UTC, porque el
        # valor sale del NOW() del servidor con la sesion en UTC.
        #
        # Sobre una columna naive, `timezone('America/Asuncion', col)` hace la
        # conversion INVERSA: interpreta el naive como hora de Asuncion y
        # devuelve un timestamptz. Hay que marcarlo como UTC primero y recien
        # despues pasarlo a hora local. Y el borde de la ventana viaja naive,
        # porque comparar naive contra aware deja que Postgres elija el huso de
        # la sesion.
        # Deuda: alinear la columna con el modelo en una migracion.
        cutoff = pyt_day_start(days_ago=days - 1).astimezone(timezone.utc)
        cutoff = cutoff.replace(tzinfo=None)
        dia_local = func.timezone(
            PYT_SQL_ZONE, func.timezone(_UTC_SQL_ZONE, BotError.created_at)
        )
        result = await db.execute(
            select(
                # cast(created_at, Date) cortaba el dia en UTC: todo lo
                # posterior a las 21:00 PYT caia en la fecha siguiente.
                cast(dia_local, Date).label("day"),
                func.count().label("cnt"),
            )
            .where(BotError.created_at >= cutoff)
            .group_by("day")
            .order_by("day")
        )
        return [{"day": str(row.day), "count": row.cnt} for row in result]


bot_error_repo = BotErrorRepository()

"""La ventana de las series diarias son N dias de calendario, no N por 24 horas.

`count_per_day` cortaba en `now - timedelta(days=days)`. Eso es un instante,
no un dia: a las 17:30 UTC con `days=7` la consulta abarcaba desde las 17:30
de hace siete dias, o sea **ocho fechas distintas** — un pedazo del dia mas
viejo entraba entero al bucket. La card decia "ultimos 7d" y dibujaba ocho
barras, la primera siempre mas corta que la realidad porque le faltaban las
primeras 17 horas y media de ese dia.

Ahora el corte es la medianoche PARAGUAYA de hace `days - 1` dias, que es la
misma ventana que usa `contact_repo.weekly_evolution`. Las tres series del
/stats hablan del mismo periodo Y del mismo huso (tanda 12): con medianoche
UTC el corte caia a las 21:00 locales del dia anterior.

El test mira el valor con el que se construyo la consulta, no el texto del
SQL: lo que importa es que sea medianoche, que NO sea UTC, y que la distancia
al hoy paraguayo sea exactamente `days - 1` dias.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.bot_error_repo import bot_error_repo
from app.repositories.message_repo import message_repo
from app.tz import PYT


def _cutoff_de_la_consulta(db_mock: AsyncMock) -> datetime:
    """El cutoff es el unico bind datetime de la consulta."""
    stmt = db_mock.execute.call_args.args[0]
    binds = [
        v for v in stmt.compile().params.values()
        if isinstance(v, datetime)
    ]
    assert len(binds) == 1, f"se esperaba un solo datetime en la consulta, hay {binds}"
    return binds[0]


async def _correr(repo, days: int) -> datetime:
    result_mock = MagicMock()
    result_mock.__iter__ = lambda self: iter(())
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)
    await repo.count_per_day(db, days=days)
    return _cutoff_de_la_consulta(db)


def _como_instante_paraguayo(cutoff: datetime, nombre: str) -> datetime:
    """El cutoff visto en hora de Paraguay, venga aware o naive.

    `messages.created_at` es `timestamptz` y el borde viaja aware. Pero
    `bot_errors.created_at` quedo `timestamp WITHOUT time zone` —el modelo
    declara timezone=True y miente— y lo que guarda es UTC. Ahi el borde tiene
    que viajar naive: comparar naive contra aware deja que Postgres resuelva el
    huso con el de la sesion, que es justo lo que la tanda 12 vino a sacar del
    medio.

    O sea que las dos formas son correctas y se ven distintas. Lo que el test
    tiene que comparar es el INSTANTE, no como esta escrito: en las dos, el
    corte es la medianoche paraguaya.
    """
    if cutoff.tzinfo is not None:
        assert nombre == "mensajes", (
            f"el corte de {nombre} viaja aware contra una columna naive: "
            "Postgres va a castearlo con el huso de la sesion"
        )
        return cutoff.astimezone(PYT)
    assert nombre == "errores", (
        f"el corte de {nombre} viaja naive contra una columna timestamptz"
    )
    return cutoff.replace(tzinfo=timezone.utc).astimezone(PYT)


@pytest.mark.parametrize("nombre", ["mensajes", "errores"])
@pytest.mark.parametrize("days", [1, 7, 30, 90])
async def test_el_corte_es_medianoche_y_cubre_exactamente_los_dias_pedidos(nombre, days):
    repo = message_repo if nombre == "mensajes" else bot_error_repo
    cutoff = await _correr(repo, days)
    local = _como_instante_paraguayo(cutoff, nombre)

    assert (local.hour, local.minute, local.second, local.microsecond) == (0, 0, 0, 0), (
        f"el corte de {nombre} no es medianoche paraguaya: {cutoff} = {local} PYT"
    )
    hoy = datetime.now(PYT).date()
    assert (hoy - local.date()).days == days - 1, (
        f"con days={days} la ventana de {nombre} cubre "
        f"{(hoy - local.date()).days + 1} dias de calendario, no {days}"
    )

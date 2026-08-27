"""Las series temporales del panel cortan el dia/mes en hora de Paraguay.

Tanda 12 del roadmap. Medido antes del cambio: 89 de 758 mensajes (11,7%)
caian en un dia distinto segun el huso. PYT es UTC-3, asi que TODO lo que
pasa entre las 21:00 y las 23:59 locales es "manana" en UTC.

EL TIMESTAMP DE PRUEBA, Y POR QUE ESTE Y NO OTRO
------------------------------------------------
``pyt_day_start() - 90 min`` es la medianoche paraguaya de hoy menos hora y
media: **ayer 22:30 PYT**, que en UTC es **hoy 01:30**. Es decir, un instante
cuyo dia paraguayo y cuyo dia UTC son SIEMPRE distintos, y que siempre esta
en el pasado y siempre dentro de una ventana de 7 dias. Un timestamp de
mediodia no probaria nada: los dos husos coinciden ahi.

Lo mismo para el mes: ``pyt_month_start() - 90 min`` es el ultimo dia del mes
anterior a las 22:30 PYT, que en UTC es el 1° de este mes.

Cada test DB-backed mide un DELTA (consulta, inserta, consulta) porque la base
de test es compartida y tiene datos de otras corridas: el numero absoluto no
es estable, la diferencia si.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.repositories.bot_error_repo import bot_error_repo
from app.repositories.contact_repo import contact_repo
from app.repositories.conversation_repo import conversation_repo
from app.repositories.lead_event_repo import lead_event_repo
from app.repositories.lead_repo import lead_repo
from app.repositories.message_repo import message_repo
from app.repositories.metrics_repository import MetricsRepository
from app.services import ai_metrics_service
from app.tz import PYT, pyt_day_start, pyt_month_start

# Prefijo que el cleanup de conftest ya reconoce como dato de test.
_PHONE_PREFIX = "+5959817"


def _cruce_de_dia() -> datetime:
    """Ayer 22:30 PYT = hoy 01:30 UTC. Dia PYT != dia UTC, siempre."""
    return pyt_day_start() - timedelta(minutes=90)


def _cruce_de_mes() -> datetime:
    """Ultimo dia del mes pasado 22:30 PYT = dia 1 de este mes 01:30 UTC."""
    return pyt_month_start() - timedelta(minutes=90)


@pytest_asyncio.fixture
async def borrar_al_final(db):
    """Junta (tabla, id) y los borra al terminar el test.

    ``bot_errors`` y ``anthropic_api_calls`` NO estan en el cleanup de
    sesion de conftest, asi que sin esto las filas quedarian para siempre.
    """
    basura: list[tuple[str, int]] = []
    yield basura
    for tabla, fila_id in reversed(basura):
        await db.execute(
            text(f"DELETE FROM {tabla} WHERE id = :id"), {"id": fila_id}
        )


async def _insertar(db, basura, sql: str, params: dict) -> int:
    tabla = sql.split("INTO", 1)[1].split("(", 1)[0].strip()
    fila_id = (await db.execute(text(sql), params)).scalar_one()
    basura.append((tabla, fila_id))
    return fila_id


async def _nuevo_contacto(db, basura, created_at: datetime, **extra) -> int:
    """Contacto de test con telefono del rango que el cleanup reconoce.

    ``source`` va explicito y NUNCA NULL: count_today filtra con
    ``source != 'import:excel'`` y en SQL ``NULL != 'x'`` es NULL, o sea que
    un contacto sin source no lo cuenta nadie y el test daria verde por la
    razon equivocada.
    """
    cols = {
        "created_at": created_at,
        "phone": f"{_PHONE_PREFIX}{uuid4().int % 10_000_000:07d}",
        "source": "whatsapp",
    }
    cols.update(extra)
    nombres = ", ".join(cols)
    valores = ", ".join(f":{k}" for k in cols)
    return await _insertar(
        db, basura,
        f"INSERT INTO contacts (name, status, {nombres}) "
        f"VALUES ('pytest_pyt', 'new', {valores}) RETURNING id",
        cols,
    )


async def _nueva_conversacion(db, basura, contacto: int) -> int:
    """messages.conversation_id es NOT NULL: no hay mensaje sin conversacion."""
    return await _insertar(
        db, basura,
        "INSERT INTO conversations (contact_id) VALUES (:c) RETURNING id",
        {"c": contacto},
    )


def _naive_utc(dt: datetime) -> datetime:
    """El mismo instante, como timestamp naive en UTC.

    ``bot_errors.created_at`` es la unica de estas columnas que quedo
    ``timestamp WITHOUT time zone`` — el modelo declara ``timezone=True`` y
    miente. asyncpg rechaza un datetime aware contra una columna naive, y lo
    que la tabla guarda es UTC (sale del ``NOW()`` del servidor con la sesion
    en UTC).
    """
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# app/tz.py — los bordes calculados en Python
# ---------------------------------------------------------------------------

class TestBordesEnPython:
    """El instante peligroso: 01:30 UTC es todavia ayer en Paraguay."""

    # 2026-08-22 01:30 UTC == 2026-08-21 22:30 PYT.
    BORDE = datetime(2026, 8, 22, 1, 30, tzinfo=timezone.utc)

    def test_el_borde_elegido_cruza_de_dia(self):
        """Prueba de que el caso de prueba prueba algo."""
        assert self.BORDE.date() != self.BORDE.astimezone(PYT).date()

    def test_dia_de_hoy_es_el_dia_paraguayo(self):
        inicio = pyt_day_start(now=self.BORDE)
        assert (inicio.year, inicio.month, inicio.day) == (2026, 8, 21)
        assert (inicio.hour, inicio.minute, inicio.second) == (0, 0, 0)

    def test_dia_de_hoy_no_es_medianoche_utc(self):
        inicio = pyt_day_start(now=self.BORDE)
        assert inicio.utcoffset() != timedelta(0)
        assert inicio.astimezone(timezone.utc) == datetime(
            2026, 8, 21, 3, 0, tzinfo=timezone.utc
        )

    def test_dias_atras_resta_dias_calendario_locales(self):
        assert pyt_day_start(days_ago=6, now=self.BORDE).day == 15

    def test_mes_en_curso_es_el_mes_paraguayo(self):
        """01:30 UTC del 1° de septiembre es todavia agosto en Paraguay."""
        borde_de_mes = datetime(2026, 9, 1, 1, 30, tzinfo=timezone.utc)
        inicio = pyt_month_start(now=borde_de_mes)
        assert (inicio.year, inicio.month, inicio.day) == (2026, 8, 1)

    def test_sin_now_usa_el_hoy_paraguayo(self):
        assert pyt_day_start().date() == datetime.now(PYT).date()


# ---------------------------------------------------------------------------
# Series diarias
# ---------------------------------------------------------------------------

class TestSerieDiariaDeContactos:
    """contact_repo.weekly_evolution — el grafico del dashboard."""

    async def test_el_contacto_de_las_2230_cae_en_ayer(self, db, borrar_al_final):
        cruce = _cruce_de_dia()
        dia_pyt = cruce.astimezone(PYT).date()
        dia_utc = cruce.astimezone(timezone.utc).date()
        assert dia_pyt != dia_utc

        antes = dict(await contact_repo.weekly_evolution(db))
        await _nuevo_contacto(db, borrar_al_final, cruce)
        despues = dict(await contact_repo.weekly_evolution(db))

        assert despues.get(dia_pyt, 0) - antes.get(dia_pyt, 0) == 1
        assert despues.get(dia_utc, 0) - antes.get(dia_utc, 0) == 0


class TestNuevosHoy:
    """contact_repo.count_today — la card "nuevos hoy"."""

    async def test_solo_cuenta_lo_de_hoy_en_paraguay(self, db, borrar_al_final):
        """Dos contactos, uno a cada lado de la medianoche paraguaya.

        Ayer 22:30 PYT (= hoy 01:30 UTC) NO cuenta; hoy 00:30 PYT (= hoy
        03:30 UTC) SI. Con ``CURRENT_DATE`` el resultado era 2 o 0 segun la
        hora de la corrida, nunca 1.
        """
        antes = await contact_repo.count_today(db)
        await _nuevo_contacto(db, borrar_al_final, _cruce_de_dia())
        await _nuevo_contacto(
            db, borrar_al_final, pyt_day_start() + timedelta(minutes=30),
        )
        assert await contact_repo.count_today(db) - antes == 1


class TestVentanaDeEventos:
    """lead_event_repo.count_by_type_this_week — el borde de los 7 dias."""

    async def test_el_borde_es_medianoche_paraguaya(self, db, borrar_al_final):
        """Dos eventos alrededor del borde: entra exactamente uno."""
        contacto = await _nuevo_contacto(db, borrar_al_final, datetime.now(timezone.utc))
        borde = pyt_day_start(days_ago=7)
        tipo = "pytest_pyt_borde"

        for cuando in (borde - timedelta(seconds=1), borde):
            await _insertar(
                db, borrar_al_final,
                "INSERT INTO lead_events (contact_id, event_type, triggered_by, "
                "created_at) VALUES (:c, :t, 'pytest', :ts) RETURNING id",
                {"c": contacto, "t": tipo, "ts": cuando},
            )

        conteos = await lead_event_repo.count_by_type_this_week(db)
        assert conteos.get(tipo, 0) == 1


class TestSerieDiariaDeMensajes:
    """message_repo.count_per_day y bot_error_repo.count_per_day."""

    async def test_el_mensaje_de_las_2230_cae_en_ayer(self, db, borrar_al_final):
        cruce = _cruce_de_dia()
        dia_pyt = str(cruce.astimezone(PYT).date())
        dia_utc = str(cruce.astimezone(timezone.utc).date())

        contacto = await _nuevo_contacto(db, borrar_al_final, cruce)
        conversacion = await _nueva_conversacion(db, borrar_al_final, contacto)
        antes = {f["day"]: f["count"] for f in await message_repo.count_per_day(db)}
        await _insertar(
            db, borrar_al_final,
            "INSERT INTO messages (contact_id, conversation_id, direction, "
            "sender_type, body, created_at) "
            "VALUES (:c, :conv, 'inbound', 'contact', 'hola', :ts) RETURNING id",
            {"c": contacto, "conv": conversacion, "ts": cruce},
        )
        despues = {f["day"]: f["count"] for f in await message_repo.count_per_day(db)}

        assert despues.get(dia_pyt, 0) - antes.get(dia_pyt, 0) == 1
        assert despues.get(dia_utc, 0) - antes.get(dia_utc, 0) == 0

    async def test_el_error_de_las_2230_cae_en_ayer(self, db, borrar_al_final):
        cruce = _cruce_de_dia()
        dia_pyt = str(cruce.astimezone(PYT).date())
        dia_utc = str(cruce.astimezone(timezone.utc).date())

        antes = {f["day"]: f["count"] for f in await bot_error_repo.count_per_day(db)}
        await _insertar(
            db, borrar_al_final,
            "INSERT INTO bot_errors (workflow, error_message, created_at) "
            "VALUES ('pytest_pyt', 'x', :ts) RETURNING id",
            {"ts": _naive_utc(cruce)},
        )
        despues = {f["day"]: f["count"] for f in await bot_error_repo.count_per_day(db)}

        assert despues.get(dia_pyt, 0) - antes.get(dia_pyt, 0) == 1
        assert despues.get(dia_utc, 0) - antes.get(dia_utc, 0) == 0


class TestSerieDiariaDeTokens:
    """ai_metrics_service.get_last_7_days_tokens_by_day — pestaña Detalle."""

    async def test_los_tokens_de_las_2230_caen_en_ayer(self, db, borrar_al_final):
        cruce = _cruce_de_dia()
        dia_pyt = str(cruce.astimezone(PYT).date())
        dia_utc = str(cruce.astimezone(timezone.utc).date())

        contacto = await _nuevo_contacto(db, borrar_al_final, cruce)
        conversacion = await _nueva_conversacion(db, borrar_al_final, contacto)
        antes = {
            f["date"]: f["tokens_in"]
            for f in await ai_metrics_service.get_last_7_days_tokens_by_day(db)
        }
        await _insertar(
            db, borrar_al_final,
            "INSERT INTO messages (contact_id, conversation_id, direction, "
            "sender_type, body, ai_model, ai_tokens_in, ai_tokens_out, "
            "created_at) "
            "VALUES (:c, :conv, 'outbound', 'bot', 'hola', 'claude-haiku-4-5', "
            "1234, 7, :ts) RETURNING id",
            {"c": contacto, "conv": conversacion, "ts": cruce},
        )
        despues = {
            f["date"]: f["tokens_in"]
            for f in await ai_metrics_service.get_last_7_days_tokens_by_day(db)
        }

        assert despues.get(dia_pyt, 0) - antes.get(dia_pyt, 0) == 1234
        assert despues.get(dia_utc, 0) - antes.get(dia_utc, 0) == 0


class TestSerieDiariaDeCosto:
    """metrics_repository.ai_cost_by_day_last_7d — el sparkline de costo IA."""

    async def test_la_llamada_de_las_2230_cae_en_ayer(self, db, borrar_al_final):
        cruce = _cruce_de_dia()
        dia_pyt = cruce.astimezone(PYT).date()
        dia_utc = cruce.astimezone(timezone.utc).date()
        repo = MetricsRepository(db)

        antes = {f.date: f.calls for f in await repo.ai_cost_by_day_last_7d()}
        await _insertar(
            db, borrar_al_final,
            "INSERT INTO anthropic_api_calls (source, model, tokens_in, "
            "tokens_out, cost_usd, created_at) VALUES ('pytest_pyt', "
            "'claude-haiku-4-5', 10, 5, 0.5, :ts) RETURNING id",
            {"ts": cruce},
        )
        despues = {f.date: f.calls for f in await repo.ai_cost_by_day_last_7d()}

        assert despues.get(dia_pyt, 0) - antes.get(dia_pyt, 0) == 1
        assert despues.get(dia_utc, 0) - antes.get(dia_utc, 0) == 0

    async def test_la_ventana_de_hoy_arranca_en_medianoche_paraguaya(
        self, db, monkeypatch,
    ):
        """El "hoy" de la card de costo no es medianoche UTC."""
        capturado: list[datetime] = []

        async def capturar(self_inner, since: datetime):
            capturado.append(since)
            return []

        monkeypatch.setattr(
            MetricsRepository, "_ai_cost_by_source_for_window", capturar,
        )
        await MetricsRepository(db).ai_cost_by_source_today()

        assert len(capturado) == 1
        inicio = capturado[0]
        assert inicio.utcoffset() != timedelta(0)
        assert (inicio.hour, inicio.minute) == (0, 0)
        assert inicio.astimezone(PYT).date() == datetime.now(PYT).date()


# ---------------------------------------------------------------------------
# Series mensuales
# ---------------------------------------------------------------------------

class TestSerieMensualDeBusquedas:
    """conversation_repo.get_demand_filter_monthly_counts."""

    async def test_la_busqueda_del_ultimo_dia_cae_en_el_mes_anterior(
        self, db, borrar_al_final,
    ):
        cruce = _cruce_de_mes()
        mes_pyt = cruce.astimezone(PYT).replace(day=1).date()
        mes_utc = cruce.astimezone(timezone.utc).replace(day=1).date()
        assert mes_pyt != mes_utc

        def por_mes(filas):
            return {f["month"].date(): f["n"] for f in filas}

        contacto = await _nuevo_contacto(db, borrar_al_final, cruce)
        antes = por_mes(await conversation_repo.get_demand_filter_monthly_counts(db))
        await _insertar(
            db, borrar_al_final,
            "INSERT INTO conversations (contact_id, channel, search_context, "
            "updated_at) VALUES (:c, 'whatsapp', "
            "'{\"filtros\": {\"ciudad\": \"pytest_pyt\"}}'::jsonb, :ts) RETURNING id",
            {"c": contacto, "ts": cruce},
        )
        despues = por_mes(await conversation_repo.get_demand_filter_monthly_counts(db))

        assert despues.get(mes_pyt, 0) - antes.get(mes_pyt, 0) == 1
        assert despues.get(mes_utc, 0) - antes.get(mes_utc, 0) == 0


class TestSerieMensualDeDemanda:
    """lead_repo.get_demand_monthly_counts."""

    async def test_el_lead_del_ultimo_dia_cae_en_el_mes_anterior(
        self, db, borrar_al_final,
    ):
        propiedad = (
            await db.execute(text("SELECT id FROM properties LIMIT 1"))
        ).scalar_one_or_none()
        if propiedad is None:
            pytest.skip(
                "la tabla properties esta vacia: la base de test no fue sembrada "
                "con scripts/seed_test.sql y este test necesita el JOIN"
            )

        cruce = _cruce_de_mes()
        mes_pyt = cruce.astimezone(PYT).replace(day=1).date()
        mes_utc = cruce.astimezone(timezone.utc).replace(day=1).date()

        def por_mes(filas):
            return {f["month"].date(): f["n"] for f in filas}

        antes = por_mes(await lead_repo.get_demand_monthly_counts(db))
        await _nuevo_contacto(
            db, borrar_al_final, cruce,
            source="whatsapp", property_id=propiedad,
        )
        despues = por_mes(await lead_repo.get_demand_monthly_counts(db))

        assert despues.get(mes_pyt, 0) - antes.get(mes_pyt, 0) == 1
        assert despues.get(mes_utc, 0) - antes.get(mes_utc, 0) == 0

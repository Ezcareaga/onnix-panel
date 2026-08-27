"""«Conversaciones trabadas» deja de ser un numero que no lleva a ningun lado.

Era el unico KPI verdaderamente operativo de /stats/health y no se podia tocar:
te decia que habia cuatro clientes esperando y no te dejaba ir a ninguno.

Lo que fija este archivo NO es que el link exista —eso es una linea de HTML—,
es que **el numero y la lista salgan del mismo predicado**. Un contador que
dice 4 sobre una lista que muestra 6 es peor que no tener el contador, y en
este repo todo lo que estaba escrito dos veces ya habia divergido. Por eso
`ConversationRepository.stuck_clause()` es un solo lugar y los dos caminos lo
LLAMAN: el test lo espia en vez de comparar dos SQL parecidos.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.conversation_repo import (
    STUCK_MINUTES,
    STUCK_WINDOW,
    ConversationRepository,
)
from app.repositories.metrics_repository import MetricsRepository


def _db_vacia() -> AsyncMock:
    result = MagicMock()
    result.all.return_value = []
    result.scalar.return_value = 0
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _sql(db: AsyncMock) -> str:
    """El SQL con los valores adentro.

    Sin `literal_binds` los literales salen como `%(param_3)s` y un
    `assert "'inbound' not in sql"` pasa siempre: verde que no prueba nada.
    """
    stmt = db.execute.await_args_list[0].args[0]
    texto = str(stmt.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
    ))
    return re.sub(r"\s+", " ", texto).lower()


# ── el predicado, una sola vez ───────────────────────────────────────────────

def test_el_predicado_dice_las_cuatro_condiciones():
    sql = re.sub(
        r"\s+", " ",
        str(ConversationRepository.stuck_clause().compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
        )),
    ).lower()
    assert "conversations.is_open is true" in sql
    assert "= 'inbound'" in sql
    assert sql.count("messages.created_at desc") == 3, (
        "el predicado mira la direccion y la fecha del ULTIMO mensaje: "
        f"tendria que haber tres subconsultas ordenadas, hay {sql.count('messages.created_at desc')}"
    )


def test_la_ventana_es_de_diez_minutos_y_veinticuatro_horas():
    """Los dos cortes, medidos contra el reloj y no leidos de un comentario."""
    ahora = datetime.now(timezone.utc)
    binds = [
        v for v in ConversationRepository.stuck_clause().compile().params.values()
        if isinstance(v, datetime)
    ]
    assert len(binds) == 2, f"se esperaban dos cortes, hay {binds}"
    distancias = sorted((ahora - b).total_seconds() for b in binds)
    assert abs(distancias[0] - STUCK_MINUTES * 60) < 5, (
        f"el corte corto esta a {distancias[0]}s, tendria que estar a {STUCK_MINUTES * 60}s"
    )
    assert abs(distancias[1] - STUCK_WINDOW.total_seconds()) < 5, (
        f"la ventana esta en {distancias[1]}s, tendria que estar en "
        f"{STUCK_WINDOW.total_seconds()}s"
    )


@pytest.mark.parametrize(
    "camino",
    ["kpi", "lista", "busqueda"],
)
async def test_los_tres_caminos_llaman_al_mismo_predicado(camino, monkeypatch):
    """Ninguno se escribe su propia copia.

    Si alguno vuelve a armar el predicado a mano el espia no se dispara, y
    ahi es donde el contador y la lista empiezan a decir numeros distintos.
    """
    original = ConversationRepository.stuck_clause
    llamadas: list[int] = []

    def espia():
        llamadas.append(1)
        return original()

    monkeypatch.setattr(ConversationRepository, "stuck_clause", staticmethod(espia))

    db = _db_vacia()
    if camino == "kpi":
        await MetricsRepository(db).count_stuck_conversations()
    elif camino == "lista":
        await ConversationRepository.get_with_contacts(db, stuck=True)
    else:
        await ConversationRepository.search_with_contacts(db, "ana", stuck=True)

    assert llamadas, f"el camino «{camino}» no usa stuck_clause(): tiene su propia copia"


# ── el filtro entra solo cuando se lo pide ───────────────────────────────────

@pytest.mark.parametrize("metodo", ["get_with_contacts", "search_with_contacts"])
async def test_sin_el_filtro_la_consulta_no_lo_trae(metodo):
    db = _db_vacia()
    fn = getattr(ConversationRepository, metodo)
    await (fn(db) if metodo == "get_with_contacts" else fn(db, "ana"))
    assert "'inbound'" not in _sql(db), (
        f"{metodo} filtra por trabadas sin que se lo pidan"
    )


@pytest.mark.parametrize("metodo", ["get_with_contacts", "search_with_contacts"])
async def test_con_el_filtro_la_consulta_lo_trae(metodo):
    db = _db_vacia()
    fn = getattr(ConversationRepository, metodo)
    await (fn(db, stuck=True) if metodo == "get_with_contacts" else fn(db, "ana", stuck=True))
    sql = _sql(db)
    assert "is_open is true" in sql
    assert "inbound" in sql


async def test_el_filtro_de_trabadas_convive_con_el_de_canal():
    """Los dos filtros son ortogonales: elegir uno no puede apagar el otro."""
    db = _db_vacia()
    await ConversationRepository.get_with_contacts(db, channel="whatsapp", stuck=True)
    sql = _sql(db)
    assert "conversations.channel = " in sql
    assert "is_open is true" in sql


async def test_el_filtro_de_trabadas_no_se_come_el_de_agente():
    """ROLE-04: un asesor sigue viendo solo sus contactos, tambien acá."""
    db = _db_vacia()
    await ConversationRepository.get_with_contacts(db, agent_filter=7, stuck=True)
    sql = _sql(db)
    assert "contacts.agent_user_id = " in sql
    assert "is_open is true" in sql


async def test_las_conversaciones_fantasma_siguen_afuera_con_el_filtro():
    """El bloque de fantasmas estaba escrito dos veces; ahora es uno solo."""
    db = _db_vacia()
    await ConversationRepository.get_with_contacts(db, stuck=True)
    assert "message_count = " in _sql(db)


# ── contra la base: el numero y la lista cuentan lo mismo ────────────────────

async def _sembrar(db, *, minutos_atras: float, direccion: str, abierta: bool = True):
    """Una conversacion con un solo mensaje, a N minutos de ahora."""
    from app.models.contact import Contact
    from app.models.conversation import Conversation
    from app.models.message import Message

    cuando = datetime.now(timezone.utc) - timedelta(minutes=minutos_atras)
    contacto = Contact(phone=None, source="manual", status="new",
                       created_at=cuando)
    db.add(contacto)
    await db.flush()
    conv = Conversation(contact_id=contacto.id, channel="whatsapp", status="active",
                        is_bot_active=True, is_open=abierta, message_count=1,
                        last_message_at=cuando, created_at=cuando, updated_at=cuando)
    db.add(conv)
    await db.flush()
    db.add(Message(conversation_id=conv.id, contact_id=contacto.id,
                   direction=direccion,
                   sender_type="contact" if direccion == "inbound" else "bot",
                   body="hola", created_at=cuando))
    await db.flush()
    return conv.id


async def test_el_numero_del_kpi_y_la_lista_no_pueden_separarse(db):
    """Cinco casos alrededor del borde, y el contador se mueve exactamente uno.

    Se mide el delta y no el total porque `onnix_dev` es un snapshot de
    produccion: el absoluto depende de lo que haya adentro, el delta no.
    """
    antes = await MetricsRepository(db).count_stuck_conversations()

    trabada = await _sembrar(db, minutos_atras=STUCK_MINUTES + 5, direccion="inbound")
    contestada = await _sembrar(db, minutos_atras=STUCK_MINUTES + 5, direccion="outbound")
    recien = await _sembrar(db, minutos_atras=1, direccion="inbound")
    vieja = await _sembrar(
        db, minutos_atras=STUCK_WINDOW.total_seconds() / 60 + 60, direccion="inbound",
    )
    cerrada = await _sembrar(
        db, minutos_atras=STUCK_MINUTES + 5, direccion="inbound", abierta=False,
    )

    despues = await MetricsRepository(db).count_stuck_conversations()
    assert despues - antes == 1, (
        "de las cinco sembradas solo una esta trabada; el contador se movio "
        f"{despues - antes}"
    )

    filas = await ConversationRepository.get_with_contacts(db, limit=5000, stuck=True)
    ids = {f["conversation"].id for f in filas}
    assert trabada in ids, "la trabada no aparece en la lista filtrada"
    for nombre, conv_id in (
        ("contestada", contestada), ("recien llegada", recien),
        ("mas vieja que la ventana", vieja), ("cerrada", cerrada),
    ):
        assert conv_id not in ids, f"la conversacion {nombre} entro a la lista de trabadas"


async def test_sin_el_filtro_la_lista_trae_las_que_no_estan_trabadas(db):
    """La prueba negativa: sin `stuck` las cinco siguen estando."""
    trabada = await _sembrar(db, minutos_atras=STUCK_MINUTES + 5, direccion="inbound")
    contestada = await _sembrar(db, minutos_atras=STUCK_MINUTES + 5, direccion="outbound")

    filas = await ConversationRepository.get_with_contacts(db, limit=5000)
    ids = {f["conversation"].id for f in filas}
    assert {trabada, contestada} <= ids


# ── la pantalla ──────────────────────────────────────────────────────────────

async def test_la_lista_filtrada_responde_y_marca_el_chip(admin_client):
    resp = await admin_client.get("/conversations/list", params={"stuck": "1"})
    assert resp.status_code == 200
    html = resp.text
    assert 'aria-pressed="true"' in html, "el chip «Trabadas» no queda marcado"
    assert 'name="stuck" value="1"' in html, (
        "sin el input escondido el buscador y el «cargar mas» pierden el filtro"
    )


async def test_sin_el_filtro_el_chip_queda_apagado(admin_client):
    resp = await admin_client.get("/conversations/list")
    assert resp.status_code == 200
    assert 'aria-pressed="false"' in resp.text
    assert 'name="stuck" value=""' in resp.text


async def test_la_pantalla_entera_acepta_el_parametro(admin_client):
    """Es la URL a la que linkea el KPI de /stats/health."""
    resp = await admin_client.get("/conversations", params={"stuck": "1"})
    assert resp.status_code == 200
    assert 'aria-pressed="true"' in resp.text

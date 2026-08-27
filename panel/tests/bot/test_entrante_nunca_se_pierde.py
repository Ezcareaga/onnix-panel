"""Un mensaje entrante valido se persiste pase lo que pase despues.

Este es EL invariante del producto. Onnix no quiere un bot: quiere que todo lo
que entra por WhatsApp, Instagram o Messenger aparezca en la bandeja para que
una persona conteste. Un entrante que no se guarda es una consulta perdida, y
no hay reintento que la traiga de vuelta.

Historia, porque explica por que el orden del webhook es el que es: hasta el
2026-08-24 el INSERT del entrante vivia DEBAJO del orquestador, o sea debajo
del armado del grafo de IA, de `bot_enabled`, de `whatsapp_mode` y del cooldown
humano. Cinco caminos podian cortar el turno y los cinco se llevaban el
mensaje. El peor era `is_bot_active`, que `reply_service` apaga en CADA
respuesta manual del panel: despues de que un asesor contestaba una vez, todo
mensaje siguiente de ese cliente desaparecia.

El grafo ya no existe —se fue con el bot— pero el invariante quedo, y con el
envio manual importa mas que antes: la bandeja es ahora lo unico que hay. Lo
que estos tests fijan es que `persist_inbound` corre ANTES que cualquier otra
cosa del webhook y que su commit es propio, asi que ningun fallo posterior
—el SSE, el registro de errores, lo que venga— puede deshacerlo.

Ninguno mockea el guardado: todos leen la fila de `messages` en la base de test.
"""
from __future__ import annotations

import itertools
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from app.bot.core.types import BotRequest
from app.bot.webhooks.whatsapp import _process_whatsapp

# Un telefono por test: el cleanup de conftest borra el rango +5959815%, pero
# corre al final de la sesion, asi que dos tests que compartan numero comparten
# conversacion — y `message_count` deja de ser verificable.
_SECUENCIA = itertools.count(1)


@pytest.fixture
def telefono() -> str:
    return f"+59598155{next(_SECUENCIA):05d}"


def _request(telefono: str, texto: str, sid: str) -> BotRequest:
    return BotRequest(
        platform="whatsapp",
        chat_id=telefono,
        user_id=telefono,
        user_name="Pytest Entrante",
        text=texto,
        external_id=sid,
        callback_data=None,
    )


async def _entrantes(db, telefono: str) -> list:
    result = await db.execute(
        text(
            "SELECT m.id, m.body, m.direction, m.external_id "
            "FROM messages m JOIN contacts c ON c.id = m.contact_id "
            "WHERE c.phone = :phone AND m.direction = 'inbound' "
            "ORDER BY m.id"
        ),
        {"phone": telefono},
    )
    return list(result.fetchall())


@pytest.fixture(autouse=True)
def sesion_sin_pool():
    """La background task pide `async_session_factory` a mano.

    El engine de la app tiene pool y pytest-asyncio da un loop por test: una
    conexion abierta en el loop del test anterior revienta con «attached to a
    different loop». `conftest._override_get_db` ya resuelve esto para las
    requests HTTP; el webhook no pasa por ahi.
    """
    from tests.conftest import _TestSession

    with patch("app.database.async_session_factory", _TestSession):
        yield


@pytest.fixture
def error_service_mudo():
    """Doble de BotErrorService.

    Sin esto un test que provoca errores del webhook podria llegar al umbral de
    `check_and_disable` y dejar `bot_enabled='false'` en la base de test — que
    el cleanup de conftest NO toca: `bot_settings` nunca se limpia.
    """
    svc = MagicMock()
    svc.record_error = AsyncMock()
    svc.check_and_disable = AsyncMock()
    with patch(
        "app.bot.services.error_service.BotErrorService", return_value=svc
    ):
        yield svc


class TestElEntranteSeGuarda:
    async def test_un_mensaje_normal_queda_en_la_bandeja(
        self, db, telefono, error_service_mudo
    ):
        await _process_whatsapp(_request(telefono, "hola, consulto", "SM-OK"))

        filas = await _entrantes(db, telefono)
        assert len(filas) == 1
        assert filas[0].body == "hola, consulto"
        assert filas[0].external_id == "SM-OK"

    async def test_no_se_contesta_solo(self, db, telefono, error_service_mudo):
        """El webhook no puede generar un saliente: Onnix contesta a mano.

        Es la contracara del test de arriba y no es redundante — un entrante
        guardado y una respuesta automatica pueden convivir, y justamente eso
        es lo que el cliente NO quiere.
        """
        await _process_whatsapp(_request(telefono, "hola", "SM-MUDO"))

        result = await db.execute(
            text(
                "SELECT count(*) FROM messages m "
                "JOIN contacts c ON c.id = m.contact_id "
                "WHERE c.phone = :phone AND m.direction = 'outbound'"
            ),
            {"phone": telefono},
        )
        assert result.scalar() == 0, (
            "el webhook genero un saliente. No hay bot: lo unico que sale del "
            "sistema lo manda una persona desde el panel"
        )

    async def test_el_segundo_mensaje_tambien_entra(
        self, db, telefono, error_service_mudo
    ):
        """El caso que se perdia con `is_bot_active` apagado.

        `reply_service` apaga ese flag en cada respuesta manual del panel, o
        sea siempre, en el modo de trabajo que Onnix quiere. Antes eso hacia
        desaparecer todo mensaje posterior del cliente.
        """
        await _process_whatsapp(_request(telefono, "primero", "SM-SEQ-1"))
        await db.execute(
            text(
                "UPDATE conversations SET is_bot_active = false "
                "WHERE contact_id = (SELECT id FROM contacts WHERE phone = :p)"
            ),
            {"p": telefono},
        )
        await db.commit()

        await _process_whatsapp(_request(telefono, "segundo", "SM-SEQ-2"))

        filas = await _entrantes(db, telefono)
        assert [f.body for f in filas] == ["primero", "segundo"]


class TestNadaPosteriorLoDeshace:
    """El commit del entrante es propio: lo que falle despues no lo revierte."""

    async def test_se_guarda_aunque_reviente_el_SSE(
        self, db, telefono, error_service_mudo
    ):
        with patch(
            "app.services.event_bus.event_bus.publish",
            side_effect=RuntimeError("el bus se cayo"),
        ):
            await _process_whatsapp(_request(telefono, "sse roto", "SM-SSE"))

        filas = await _entrantes(db, telefono)
        assert len(filas) == 1, (
            "el fallo del SSE se llevo el mensaje: el commit del entrante no "
            "es independiente del resto del webhook"
        )
        assert filas[0].body == "sse roto"

    async def test_el_duplicado_no_entra_dos_veces(
        self, db, telefono, error_service_mudo
    ):
        """Meta y Twilio reintentan un webhook que no contestaron a tiempo."""
        peticion = _request(telefono, "reintento", "SM-DUP")
        await _process_whatsapp(peticion)
        await _process_whatsapp(peticion)

        filas = await _entrantes(db, telefono)
        assert len(filas) == 1, (
            f"el mismo external_id entro {len(filas)} veces; un reintento del "
            "proveedor duplica la conversacion en la bandeja"
        )

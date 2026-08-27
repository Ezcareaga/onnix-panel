"""Un mensaje entrante valido se persiste pase lo que pase despues.

Que el bot no conteste es una decision del producto. Que el mensaje no exista
es perdida de datos, y la administradora no puede perder la consulta.

Hasta el 2026-08-24 el INSERT del entrante vivia en el paso 4 del orquestador,
DEBAJO de todo lo que podia cortar el turno:

  - el armado del grafo de dependencias, que corre antes de que el orquestador
    exista y levantaba `RuntimeError` con `GEMINI_API_KEY` vacia (3a75092);
  - `bot_enabled` y `whatsapp_mode='manual'`, en MessageHandler;
  - `is_bot_active` y el cooldown humano, en el orquestador.

Cinco caminos, un solo efecto: el WhatsApp del cliente desaparecia. El peor era
`is_bot_active`, porque `reply_service` lo apaga en CADA respuesta manual del
panel: despues de que un asesor contesta una vez, todo mensaje siguiente de ese
cliente se perdia.

Estos tests fijan el invariante contra los cinco caminos. Ninguno mockea el
guardado: todos leen la fila de `messages` en la base de test.
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


@pytest.fixture
def sin_envio():
    """Ningun test de este archivo puede postear a Twilio."""
    with patch(
        "app.bot.channels.whatsapp.WhatsAppSender.send", new_callable=AsyncMock
    ) as doble:
        yield doble


@pytest.fixture
def setting_pisado():
    """Devuelve un context manager que fuerza UN valor de bot_settings.

    Se parchea el repositorio y no la base: `bot_settings` no la limpia nadie,
    y un test que escriba ahi deja la base de test con el bot apagado.
    """
    from app.repositories.bot_setting_repo import BotSettingRepository

    real = BotSettingRepository.get_value

    def _pisar(clave: str, valor: str):
        async def falso(session, key):
            if key == clave:
                return valor
            return await real(session, key)

        return patch(
            "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
            side_effect=falso,
        )

    return _pisar


class TestElGrafoRotoNoSeLlevaElMensaje:
    """El caso que vivio cinco semanas en produccion."""

    async def test_se_guarda_aunque_el_grafo_reviente(
        self, db, telefono, error_service_mudo
    ):
        with patch(
            "app.bot.webhooks.dependencies.get_bot_dependencies",
            side_effect=RuntimeError("FATAL: GEMINI_API_KEY vacia"),
        ):
            await _process_whatsapp(
                _request(telefono, "el grafo revienta", "SM-GRAFO")
            )

        filas = await _entrantes(db, telefono)
        assert len(filas) == 1, (
            "el entrante tiene que estar en messages aunque el armado del "
            "grafo haya reventado"
        )
        assert filas[0].body == "el grafo revienta"
        assert filas[0].external_id == "SM-GRAFO"

    async def test_el_error_igual_se_registra(
        self, db, telefono, error_service_mudo
    ):
        """Guardar primero no puede tapar el error: el contador lo sigue viendo."""
        with patch(
            "app.bot.webhooks.dependencies.get_bot_dependencies",
            side_effect=RuntimeError("FATAL: GEMINI_API_KEY vacia"),
        ):
            await _process_whatsapp(_request(telefono, "hola", "SM-ERR"))

        error_service_mudo.record_error.assert_awaited_once()
        assert (
            error_service_mudo.record_error.await_args.kwargs["node"]
            == "webhook_process"
        )


class TestLasCompuertasNoDescartanElEntrante:
    """Cuatro formas de decidir «no contesto», ninguna borra el mensaje."""

    async def test_bot_enabled_apagado(
        self, db, telefono, error_service_mudo, sin_envio, setting_pisado
    ):
        with setting_pisado("bot_enabled", "false"):
            await _process_whatsapp(_request(telefono, "bot apagado", "SM-BOTOFF"))

        assert len(await _entrantes(db, telefono)) == 1

    async def test_whatsapp_mode_manual(
        self, db, telefono, error_service_mudo, sin_envio, setting_pisado
    ):
        with setting_pisado("whatsapp_mode", "manual"):
            await _process_whatsapp(_request(telefono, "modo manual", "SM-MANUAL"))

        assert len(await _entrantes(db, telefono)) == 1

    async def test_is_bot_active_apagado(
        self, db, telefono, error_service_mudo, sin_envio
    ):
        """El peor de los cuatro: `reply_service` lo apaga en cada respuesta manual."""
        # Primer mensaje: crea contacto + conversacion. El grafo se corta a
        # proposito para no llamar a Claude.
        with patch(
            "app.bot.webhooks.dependencies.get_bot_dependencies",
            side_effect=RuntimeError("corte deliberado"),
        ):
            await _process_whatsapp(_request(telefono, "primero", "SM-ACT-1"))

        await db.execute(
            text(
                "UPDATE conversations SET is_bot_active = false "
                "WHERE contact_id IN (SELECT id FROM contacts WHERE phone = :p)"
            ),
            {"p": telefono},
        )
        await db.commit()

        await _process_whatsapp(_request(telefono, "segundo", "SM-ACT-2"))

        filas = await _entrantes(db, telefono)
        assert [f.body for f in filas] == ["primero", "segundo"], (
            "con is_bot_active=false el bot calla, pero el mensaje se guarda"
        )

    async def test_cooldown_humano(
        self, db, telefono, error_service_mudo, sin_envio
    ):
        with patch(
            "app.bot.webhooks.dependencies.get_bot_dependencies",
            side_effect=RuntimeError("corte deliberado"),
        ):
            await _process_whatsapp(_request(telefono, "primero", "SM-COOL-1"))

        await db.execute(
            text(
                "UPDATE conversations SET last_human_reply_at = NOW() "
                "WHERE contact_id IN (SELECT id FROM contacts WHERE phone = :p)"
            ),
            {"p": telefono},
        )
        await db.commit()

        await _process_whatsapp(_request(telefono, "segundo", "SM-COOL-2"))

        filas = await _entrantes(db, telefono)
        assert [f.body for f in filas] == ["primero", "segundo"]


class TestRedeliveryDeTwilio:
    """Guardar primero pone el ON CONFLICT en el camino de todos los reintentos."""

    async def test_el_mismo_sid_no_duplica_ni_infla_el_contador(
        self, db, telefono, error_service_mudo
    ):
        with patch(
            "app.bot.webhooks.dependencies.get_bot_dependencies",
            side_effect=RuntimeError("corte deliberado"),
        ):
            await _process_whatsapp(_request(telefono, "reintento", "SM-DUP"))
            await _process_whatsapp(_request(telefono, "reintento", "SM-DUP"))

        assert len(await _entrantes(db, telefono)) == 1

        result = await db.execute(
            text(
                "SELECT message_count FROM conversations "
                "WHERE contact_id IN (SELECT id FROM contacts WHERE phone = :p)"
            ),
            {"p": telefono},
        )
        assert result.scalar_one() == 1, (
            "el redelivery no puede sumar al contador de la conversacion"
        )


class TestElWebhookCompletoConLaKeyVacia:
    """La prueba que nadie corrio, y por eso el bug vivio cinco semanas.

    No mockea el grafo: lo arma de verdad, con `GEMINI_API_KEY` vacia, entrando
    por el POST de Twilio. `BOT_ENABLED=false` corta el turno DESPUES de armar
    el grafo (MessageHandler paso 0), asi que el test ejercita el armado sin
    llamar a Claude ni a Twilio.
    """

    async def test_post_de_twilio_con_gemini_vacia_deja_el_mensaje_en_la_base(
        self, db, client, telefono, monkeypatch, error_service_mudo
    ):
        from app.bot.config import bot_settings
        from app.bot.webhooks.dependencies import (
            get_bot_dependencies,
            reset_bot_dependencies,
        )

        monkeypatch.setenv("BOT_ENABLED", "false")
        reset_bot_dependencies()
        try:
            with patch.object(bot_settings, "GEMINI_API_KEY", ""), patch(
                "app.bot.webhooks.whatsapp._get_twilio_auth_token",
                return_value="",
            ):
                resp = await client.post(
                    "/webhook/whatsapp",
                    data={
                        "From": f"whatsapp:{telefono}",
                        "To": "whatsapp:+595900000000",
                        "Body": "Hola, vi una casa en Lambare",
                        "MessageSid": "SM-E2E-KEY-VACIA",
                        "ProfileName": "Pytest E2E",
                    },
                )
                assert resp.status_code == 200

                # El grafo se armo de verdad, y quedo degradado: sin pierna
                # vectorial. Antes de este arreglo esta linea levantaba
                # RuntimeError.
                deps = get_bot_dependencies()
                assert (
                    deps.wa_handler._orchestrator._search_service._vector_search
                    is None
                )
        finally:
            reset_bot_dependencies()

        filas = await _entrantes(db, telefono)
        assert len(filas) == 1, (
            "el POST de Twilio con la key vacia tiene que dejar el mensaje "
            "guardado"
        )
        assert filas[0].body == "Hola, vi una casa en Lambare"
        assert filas[0].external_id == "SM-E2E-KEY-VACIA"

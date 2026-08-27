"""El armado del grafo de dependencias tambien es un fallo que hay que registrar.

`_process_telegram` y `_process_whatsapp` pedian `get_bot_dependencies()`
ANTES del try. Armar ese grafo construye los clientes de Claude y de Gemini,
el buscador y los senders, asi que cualquier credencial ausente revienta ahi:
`genai.Client(api_key="")` tira ValueError apenas se instancia. Fuera del try,
esa excepcion no la agarra nadie — sale como error no manejado de la background
task, sin pasar por BotErrorService y sin llegar al contador que apaga el bot.

No es hipotetico: GEMINI_API_KEY esta vacia en el .env de produccion.

Estos tests fijan las dos garantias que el orden correcto da: que el fallo se
registra como cualquier otro error del webhook, y que no se propaga.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.core.types import BotRequest
from app.bot.webhooks.telegram import _process_telegram
from app.bot.webhooks.whatsapp import _process_whatsapp


def _request(platform: str) -> BotRequest:
    return BotRequest(
        platform=platform,
        chat_id="+595981555000",
        user_id="+595981555000",
        user_name="Juan Test",
        text="Hola",
        external_id="EXT-1",
        callback_data=None,
    )


@pytest.fixture(autouse=True)
def sesion_sin_pool():
    """La background task pide `async_session_factory` a mano.

    Desde el 2026-08-24 `_process_*` guarda el entrante antes de pedir el
    grafo, asi que estos tests tocan la base de verdad. El engine de la app
    tiene pool y pytest-asyncio da un loop por test: una conexion abierta en
    el loop anterior revienta con «attached to a different loop» y el mensaje
    que llega a `record_error` deja de ser el del grafo.
    """
    from tests.conftest import _TestSession

    with patch("app.database.async_session_factory", _TestSession):
        yield


@pytest.fixture
def error_service():
    """Doble de BotErrorService, para no escribir en la base."""
    svc = MagicMock()
    svc.record_error = AsyncMock()
    svc.check_and_disable = AsyncMock()
    with patch(
        "app.bot.services.error_service.BotErrorService", return_value=svc
    ):
        yield svc


@pytest.mark.parametrize(
    "proceso, modulo, plataforma",
    [
        (_process_telegram, "telegram", "telegram"),
        (_process_whatsapp, "whatsapp", "whatsapp"),
    ],
)
class TestFalloAlArmarElGrafo:
    async def test_no_se_propaga(self, proceso, modulo, plataforma, error_service):
        """Una background task que revienta no tiene a quien avisarle."""
        with patch(
            "app.bot.webhooks.dependencies.get_bot_dependencies",
            side_effect=ValueError("No API key was provided."),
        ):
            await proceso(_request(plataforma))  # no debe levantar

    async def test_se_registra_como_error_del_webhook(
        self, proceso, modulo, plataforma, error_service
    ):
        """Sin esto el fallo no llega al contador que apaga el bot."""
        with patch(
            "app.bot.webhooks.dependencies.get_bot_dependencies",
            side_effect=ValueError("No API key was provided."),
        ):
            await proceso(_request(plataforma))

        error_service.record_error.assert_awaited_once()
        mensaje = error_service.record_error.await_args.args[1]
        assert "No API key was provided." in mensaje
        assert error_service.record_error.await_args.kwargs["node"] == "webhook_process"

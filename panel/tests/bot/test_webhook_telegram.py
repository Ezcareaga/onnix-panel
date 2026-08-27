"""Tests for Telegram webhook endpoint.

Plan 66-01, Task 5: 12 tests covering parse_telegram_update,
verify_telegram_secret, and the POST /webhook/telegram route.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.bot.webhooks.telegram import (
    parse_telegram_update,
    router,
    verify_telegram_secret,
)
from app.bot.core.types import BotRequest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app() -> FastAPI:
    """Minimal FastAPI app with the telegram webhook router."""
    _app = FastAPI()
    _app.include_router(router)
    return _app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Test client with no secret enforced (dev mode)."""
    with patch("app.bot.webhooks.telegram.bot_settings") as mock_settings:
        mock_settings.TELEGRAM_WEBHOOK_SECRET = ""
        with TestClient(app) as c:
            yield c


# ---------------------------------------------------------------------------
# El procesamiento en background NO entra en estos tests.
#
# `background_tasks.add_task(_process_telegram, ...)` con TestClient corre
# sincronicamente dentro del request, y `_process_telegram` arma el grafo
# completo del bot: Claude, Gemini, el buscador y una sesion de base. Con
# GEMINI_API_KEY vacia eso muere en `genai.Client(api_key="")` con un
# ValueError que sale como 500, asi que los tests del route fallaban por una
# credencial ausente y no por el route.
#
# Estos tests son del webhook: parseo, secreto, status y cuerpo. Que el
# pipeline funcione es de los tests del pipeline.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _sin_pipeline():
    """Reemplaza el procesamiento en background por un doble."""
    with patch(
        "app.bot.webhooks.telegram._process_telegram", new_callable=AsyncMock
    ) as doble:
        yield doble


@pytest.fixture
def client_with_secret(app: FastAPI) -> TestClient:
    """Test client with secret enforcement enabled."""
    with patch("app.bot.webhooks.telegram.bot_settings") as mock_settings:
        mock_settings.TELEGRAM_WEBHOOK_SECRET = "test-secret-token"
        with TestClient(app) as c:
            yield c


def _make_message_update(
    text: str = "Hola",
    chat_id: int = 12345,
    user_id: int = 67890,
    first_name: str = "Juan",
    last_name: str = "Perez",
    message_id: int = 100,
    chat_type: str = "private",
) -> dict:
    """Build a minimal Telegram message Update."""
    return {
        "update_id": 999,
        "message": {
            "message_id": message_id,
            "from": {
                "id": user_id,
                "first_name": first_name,
                "last_name": last_name,
                "username": "juanp",
            },
            "chat": {"id": chat_id, "type": chat_type},
            "text": text,
        },
    }


def _make_callback_update(
    callback_data: str = "detail_42",
    chat_id: int = 12345,
    user_id: int = 67890,
    first_name: str = "Juan",
    callback_id: str = "cb_001",
    chat_type: str = "private",
) -> dict:
    """Build a minimal Telegram callback_query Update."""
    return {
        "update_id": 999,
        "callback_query": {
            "id": callback_id,
            "from": {
                "id": user_id,
                "first_name": first_name,
                "username": "juanp",
            },
            "message": {
                "message_id": 50,
                "chat": {"id": chat_id, "type": chat_type},
            },
            "data": callback_data,
        },
    }


# ---------------------------------------------------------------------------
# Tests: parse_telegram_update (Tasks 1)
# ---------------------------------------------------------------------------


class TestParseTelegramUpdate:
    """Tests for the parse_telegram_update function."""

    def test_parse_text_message(self):
        """Regular private text message is parsed correctly."""
        body = _make_message_update(text="Busco casa", chat_id=111, user_id=222)
        result = parse_telegram_update(body)

        assert result is not None
        assert isinstance(result, BotRequest)
        assert result.platform == "telegram"
        assert result.chat_id == "111"
        assert result.user_id == "222"
        assert result.user_name == "Juan Perez"
        assert result.text == "Busco casa"
        assert result.external_id == "100"
        assert result.callback_data is None

    def test_parse_caption_message(self):
        """Message with caption (photo) but no text is parsed using caption."""
        body = _make_message_update(text="ignored")
        # Remove text, add caption
        body["message"].pop("text")
        body["message"]["caption"] = "Foto de la propiedad"

        result = parse_telegram_update(body)

        assert result is not None
        assert result.text == "Foto de la propiedad"

    def test_parse_callback_query(self):
        """Callback query is parsed correctly."""
        body = _make_callback_update(
            callback_data="next_page",
            chat_id=333,
            user_id=444,
            callback_id="cb_xyz",
        )
        result = parse_telegram_update(body)

        assert result is not None
        assert result.platform == "telegram"
        assert result.chat_id == "333"
        assert result.user_id == "444"
        assert result.callback_data == "next_page"
        assert result.external_id == "cb_xyz"
        assert result.text is None

    def test_group_message_returns_none(self):
        """Messages from group chats are ignored."""
        body = _make_message_update(chat_type="group")
        result = parse_telegram_update(body)
        assert result is None

    def test_group_callback_returns_none(self):
        """Callback queries from group chats are ignored."""
        body = _make_callback_update(chat_type="supergroup")
        result = parse_telegram_update(body)
        assert result is None

    def test_edited_message_returns_none(self):
        """edited_message updates are ignored (no 'message' key)."""
        body = {
            "update_id": 999,
            "edited_message": {
                "message_id": 100,
                "from": {"id": 123, "first_name": "Test"},
                "chat": {"id": 456, "type": "private"},
                "text": "edited text",
            },
        }
        result = parse_telegram_update(body)
        assert result is None

    def test_no_text_no_caption_returns_none(self):
        """Message with no text and no caption is ignored."""
        body = _make_message_update()
        body["message"].pop("text")
        # No caption either
        result = parse_telegram_update(body)
        assert result is None

    def test_user_name_fallback_to_username(self):
        """When first_name and last_name are absent, falls back to username."""
        body = _make_message_update()
        body["message"]["from"] = {"id": 99, "username": "ghostuser"}

        result = parse_telegram_update(body)

        assert result is not None
        assert result.user_name == "ghostuser"


# ---------------------------------------------------------------------------
# Tests: verify_telegram_secret (Task 2)
# ---------------------------------------------------------------------------


class TestVerifyTelegramSecret:
    """Tests for the secret token verification dependency."""

    def test_valid_secret(self, client_with_secret: TestClient):
        """Request with correct secret header passes."""
        body = _make_message_update()
        response = client_with_secret.post(
            "/webhook/telegram",
            json=body,
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret-token"},
        )
        assert response.status_code == 200

    def test_invalid_secret(self, client_with_secret: TestClient):
        """Request with wrong secret header returns 403."""
        body = _make_message_update()
        response = client_with_secret.post(
            "/webhook/telegram",
            json=body,
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-token"},
        )
        assert response.status_code == 403

    def test_missing_secret_header(self, client_with_secret: TestClient):
        """Request without secret header returns 403 when secret is set."""
        body = _make_message_update()
        response = client_with_secret.post("/webhook/telegram", json=body)
        assert response.status_code == 403

    def test_dev_mode_skips_validation(self, client: TestClient):
        """When TELEGRAM_WEBHOOK_SECRET is empty, any request passes."""
        body = _make_message_update()
        response = client.post("/webhook/telegram", json=body)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Tests: POST /webhook/telegram route (Task 4)
# ---------------------------------------------------------------------------


class TestWebhookRoute:
    """Tests for the FastAPI route behavior."""

    def test_valid_message_returns_ok(self, client: TestClient):
        """Valid private message returns {"status": "ok"}."""
        body = _make_message_update(text="Hola")
        response = client.post("/webhook/telegram", json=body)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_valid_message_agenda_el_procesamiento(
        self, client: TestClient, _sin_pipeline
    ):
        """Nadie verificaba que el mensaje llegara al pipeline: se notaba solo
        cuando el pipeline reventaba y el 200 se volvia 500."""
        client.post("/webhook/telegram", json=_make_message_update(text="Hola"))
        _sin_pipeline.assert_awaited_once()
        (enviado,) = _sin_pipeline.await_args.args
        assert enviado.text == "Hola"
        assert enviado.platform == "telegram"

    def test_ignored_update_no_agenda_nada(
        self, client: TestClient, _sin_pipeline
    ):
        """Un mensaje de grupo se descarta antes del pipeline."""
        client.post("/webhook/telegram", json=_make_message_update(chat_type="group"))
        _sin_pipeline.assert_not_awaited()

    def test_ignored_update_returns_ignored(self, client: TestClient):
        """Group chat message returns {"status": "ignored"}."""
        body = _make_message_update(chat_type="group")
        response = client.post("/webhook/telegram", json=body)
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}

    def test_empty_update_returns_ignored(self, client: TestClient):
        """Empty/unknown update body returns {"status": "ignored"}."""
        response = client.post("/webhook/telegram", json={"update_id": 1})
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}

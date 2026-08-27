"""El guard de aislamiento de Telegram, que hasta hoy no existia.

`CLAUDE.md` lista seis guards obligatorios para staging, porque staging hereda
el `.env` de produccion y sin ellos manda mensajes a gente real — paso el
2026-04-04: 27 templates a un contacto real, Meta throttleando con 63049.

De los seis, **cuatro estaban implementados y dos no los leia nadie**:
`BOT_ENABLED` y `TELEGRAM_NOTIFICATIONS_ENABLED` aparecian en
`docker-compose.dev.yml` y en el `CLAUDE.md` y en cero lineas de codigo. Este
archivo cubre el segundo.

**El guard va donde se abre el socket**, no donde parece razonable: `_post` es
el unico lugar del modulo que sale a la red, y `_send_text`, `_send_photo` y
`_send_media_group` pasan todos por ahi. Es la leccion literal del CLAUDE.md
sobre `WA_SEND_ENABLED`: siete caminos llegaban a Twilio y seis no tenian guard.

El `chat_id` vacio es la otra mitad. Staging lo deja en blanco a proposito, y
sin chequearlo el POST salia igual —con el token real— nada mas que para que
Telegram contestara el error. Una llamada saliente a una API externa desde
staging es exactamente lo que prohibe la regla 10.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.bot.channels.telegram import TelegramSender, telegram_send_disabled


class TestElSwitch:
    @pytest.mark.parametrize("valor", ["false", "FALSE", "0", "", "  "])
    def test_apagado_en_sus_cinco_formas(self, valor, monkeypatch):
        monkeypatch.setenv("TELEGRAM_NOTIFICATIONS_ENABLED", valor)
        assert telegram_send_disabled() is True

    @pytest.mark.parametrize("valor", ["true", "TRUE", "1", "si"])
    def test_prendido(self, valor, monkeypatch):
        monkeypatch.setenv("TELEGRAM_NOTIFICATIONS_ENABLED", valor)
        assert telegram_send_disabled() is False

    def test_sin_la_variable_manda(self, monkeypatch):
        """El default tiene que ser `true`: produccion no la declara."""
        monkeypatch.delenv("TELEGRAM_NOTIFICATIONS_ENABLED", raising=False)
        assert telegram_send_disabled() is False


class TestNoSaleALaRed:
    """Lo que importa no es el booleano: es que el socket no se abra."""

    @pytest.fixture
    def cliente(self):
        c = AsyncMock()
        c.post = AsyncMock()
        return c

    async def test_con_el_switch_apagado_no_hay_post(self, cliente, monkeypatch):
        monkeypatch.setenv("TELEGRAM_NOTIFICATIONS_ENABLED", "false")
        sender = TelegramSender(bot_token="123:REAL-TOKEN")
        ok = await sender._post("sendMessage", cliente, {"chat_id": "42", "text": "hola"})
        assert ok is False
        cliente.post.assert_not_awaited()

    async def test_con_el_chat_id_vacio_no_hay_post(self, cliente, monkeypatch):
        """Es el estado real de staging: TELEGRAM_EZ_CHAT_ID= sin valor."""
        monkeypatch.setenv("TELEGRAM_NOTIFICATIONS_ENABLED", "true")
        sender = TelegramSender(bot_token="123:REAL-TOKEN")
        for vacio in ("", "   ", None):
            cliente.post.reset_mock()
            ok = await sender._post("sendMessage", cliente, {"chat_id": vacio, "text": "hola"})
            assert ok is False, f"chat_id={vacio!r} igual salio a la red"
            cliente.post.assert_not_awaited()

    async def test_prendido_y_con_chat_id_si_postea(self, cliente, monkeypatch):
        """La contracara: sin esto, un guard que bloquea TODO tambien pasa."""
        monkeypatch.setenv("TELEGRAM_NOTIFICATIONS_ENABLED", "true")
        cliente.post.return_value = type("R", (), {
            "status_code": 200, "json": lambda self: {"ok": True, "result": {"message_id": 7}},
        })()
        sender = TelegramSender(bot_token="123:REAL-TOKEN")
        ok = await sender._post("sendMessage", cliente, {"chat_id": "42", "text": "hola"})
        assert ok is True
        cliente.post.assert_awaited_once()

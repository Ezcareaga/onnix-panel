"""Los seis guards de aislamiento de staging, con la misma semantica.

`CLAUDE.md:56-63` los declara obligatorios porque staging hereda el `.env` de
produccion: sin ellos sale con credenciales reales a telefonos reales. Paso el
2026-04-04 — 27 templates a un contacto real, Meta throttleando con 63049.

`BOT_ENABLED` estaba en esta lista y se fue con el bot conversacional: sin un
bot que conteste solo, la variable no apaga nada. Los que quedan son los dos que
abren un socket hacia afuera, y con envio manual importan MAS, no menos: ahora
todo lo que sale del panel lo manda una persona.

Este archivo no prueba cada guard —cada uno tiene el suyo— sino **que los
switches de proceso se comporten igual**. Dos lecturas de `os.getenv` escritas
por separado son dos copias, y en este repo lo escrito dos veces ya divergio
cuatro veces. Si una empieza a tratar `"no"` como apagado o `""` como
encendido, un guard protege distinto que el otro y nadie se entera hasta el
incidente.
"""
from __future__ import annotations

import pytest

from app.bot.channels.telegram import telegram_send_disabled
from app.bot.channels.twilio_retry import wa_send_disabled

GUARDS = [
    ("WA_SEND_ENABLED", wa_send_disabled),
    ("TELEGRAM_NOTIFICATIONS_ENABLED", telegram_send_disabled),
]
_IDS = [n for n, _ in GUARDS]


@pytest.mark.parametrize("var,guard", GUARDS, ids=_IDS)
@pytest.mark.parametrize("apagado", ["false", "FALSE", "False", "0", "", "   "])
def test_apagado_significa_lo_mismo_para_los_tres(var, guard, apagado, monkeypatch):
    monkeypatch.setenv(var, apagado)
    assert guard() is True, f"{var}={apagado!r} tendria que contar como apagado"


@pytest.mark.parametrize("var,guard", GUARDS, ids=_IDS)
@pytest.mark.parametrize("prendido", ["true", "TRUE", "1", "yes", "si"])
def test_prendido_significa_lo_mismo_para_los_tres(var, guard, prendido, monkeypatch):
    monkeypatch.setenv(var, prendido)
    assert guard() is False, f"{var}={prendido!r} tendria que contar como prendido"


@pytest.mark.parametrize("var,guard", GUARDS, ids=_IDS)
def test_sin_la_variable_los_tres_dejan_pasar(var, guard, monkeypatch):
    """El default tiene que ser «prendido»: produccion no declara ninguna de
    las tres, y un default al reves apagaria el bot en prod al desplegar."""
    monkeypatch.delenv(var, raising=False)
    assert guard() is False, f"sin {var} el default cambio y prod se apaga"

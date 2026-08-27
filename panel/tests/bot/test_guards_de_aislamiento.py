"""Los seis guards de aislamiento de staging, con la misma semantica.

`CLAUDE.md:56-63` los declara obligatorios porque staging hereda el `.env` de
produccion: sin ellos sale con credenciales reales a telefonos reales. Paso el
2026-04-04 — 27 templates a un contacto real, Meta throttleando con 63049.

La auditoria del 2026-08-23 encontro que **dos de los seis no los leia nadie**:
`BOT_ENABLED` y `TELEGRAM_NOTIFICATIONS_ENABLED` estaban en el compose y en el
`CLAUDE.md` y en cero lineas de codigo. Los dos se implementaron ese dia.

Este archivo no prueba cada guard —cada uno tiene el suyo— sino **que los tres
switches de proceso se comporten igual**. Tres lecturas de `os.getenv` escritas
por separado son tres copias, y en este repo lo escrito dos veces ya divergio
cuatro veces. Si una empieza a tratar `"no"` como apagado o `""` como
encendido, el guard mas nuevo protege distinto que el mas viejo y nadie se
entera hasta el incidente.
"""
from __future__ import annotations

import pytest

from app.bot.channels.telegram import telegram_send_disabled
from app.bot.channels.twilio_retry import wa_send_disabled
from app.bot.handlers.message_handler import bot_disabled_by_env

GUARDS = [
    ("WA_SEND_ENABLED", wa_send_disabled),
    ("TELEGRAM_NOTIFICATIONS_ENABLED", telegram_send_disabled),
    ("BOT_ENABLED", bot_disabled_by_env),
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


def test_el_guard_de_ambiente_no_es_el_switch_de_producto():
    """`BOT_ENABLED` (entorno) y `bot_settings.bot_enabled` (DB) son dos cosas.

    El de la DB lo prende la administradora y **contesta** avisando que el asistente no
    esta disponible. El de entorno lo pone el compose de staging y **no manda
    nada**: cualquier respuesta de staging sale con las credenciales reales.

    Si alguien los unifica, staging vuelve a escribirle a un telefono real para
    decirle que el bot esta apagado.
    """
    import inspect

    from app.bot.handlers import message_handler

    fuente = inspect.getsource(message_handler.MessageHandler._handle_inner)
    corte = fuente.index("bot_disabled_by_env")
    antes = fuente[:corte]
    assert "_sender.send" not in antes, (
        "hay un envio antes del guard de BOT_ENABLED: staging manda igual"
    )
    # y el propio bloque del guard no manda nada
    bloque = fuente[corte:corte + 600]
    fin = bloque.index("Step 1")
    assert "_sender.send" not in bloque[:fin], (
        "el guard de BOT_ENABLED contesta. Tiene que ser silencioso — el que "
        "contesta es el switch de la DB"
    )

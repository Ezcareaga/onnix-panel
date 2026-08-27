"""La card «Uso de IA» decía «Claude 100%» los días en que el bot no habló.

`pct_fallback` es `gemini / (claude + gemini) * 100` y con los dos contadores
en cero vale 0, así que `100 - 0` daba 100: la pantalla afirmaba que el 100% de
las respuestas salió por Claude en un día sin ninguna llamada. El porcentaje no
era impreciso, no existía.

Importa más de lo que parece en este panel: al 2026-08-21 producción lleva un
mes con cero llamadas —los dos switches de autorespuesta están en `false` desde
abril—, así que **ese es el estado que la administradora ve todos los días**.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from app.schemas.metrics import (
    AiCost, BotHealthSnapshot, Costs, ErrorBreakdown, HeartbeatStatus, Latency,
    MessageVolume, ProviderMix, StuckConversations, ToolIterations, TwilioUsage,
)

_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


def _snapshot(claude: int, gemini: int) -> BotHealthSnapshot:
    """El snapshot real, en cero salvo la mezcla de proveedores.

    Se arma con los schemas de verdad y no con un stub: si mañana la card pide
    un campo nuevo, el test lo dice acá y no en producción.
    """
    total = claude + gemini
    pct = (gemini / total * 100) if total else 0.0
    sin_costo = AiCost(claude_usd=0.0, gemini_usd=0.0, total_usd=0.0, messages=0)
    sin_twilio = TwilioUsage(total_usd=0.0, whatsapp_usd=0.0, other_usd=0.0)
    return BotHealthSnapshot(
        generated_at=datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc),
        stuck_conversations=StuckConversations(count=0),
        message_volume=MessageVolume(inbound=0, bot_out=0, agent_out=0, total=0),
        latency=Latency(avg_ms=0, p95_ms=0, worst_ms=0, n=0),
        provider_mix=ProviderMix(claude=claude, gemini=gemini, pct_fallback=pct),
        tool_iterations=ToolIterations(avg=0.0, max=0, zero_tools=0, high_iters=0, n=0),
        heartbeat=HeartbeatStatus(last_failure_at=None),
        errors=ErrorBreakdown(by_workflow={}, total=0),
        costs=Costs(
            ai_today=sin_costo, ai_month=sin_costo,
            twilio_today=sin_twilio, twilio_month=sin_twilio,
            total_today_usd=0.0, total_month_usd=0.0,
        ),
    )


def _render(claude: int, gemini: int) -> str:
    env = Environment(
        loader=ChoiceLoader([DictLoader({}), FileSystemLoader(str(_TEMPLATES))]),
        autoescape=True,
    )
    env.filters["pyt"] = lambda d, f="%d/%m/%Y %H:%M": "—"
    return env.get_template("partials/bot_health_stats.html").render(
        snapshot=_snapshot(claude, gemini), user=SimpleNamespace(role="admin"),
    )


def _card_uso_ia(html: str) -> str:
    m = re.search(r"Uso de IA(.*?)Costo IA", html, re.S)
    assert m, "no se encontró la card «Uso de IA»"
    return m.group(1)


def test_sin_llamadas_no_dice_cien_por_ciento():
    """El bug: cero llamadas y la pantalla decía que Claude atendió todo."""
    card = _card_uso_ia(_render(claude=0, gemini=0))
    assert "100" not in card, (
        "con cero llamadas la card sigue afirmando un 100%: "
        f"{' '.join(card.split())[:200]}"
    )
    assert "Sin llamadas" in card, "el estado vacío tiene que decirse en palabras"


def test_con_llamadas_vuelve_a_haber_porcentaje():
    """La prueba negativa: el estado vacío no puede tragarse el caso normal."""
    card = _card_uso_ia(_render(claude=97, gemini=3))
    assert "Sin llamadas" not in card
    assert "97%" in card, f"no se imprimió el porcentaje de Claude: {' '.join(card.split())[:200]}"
    assert "3.0%" in card
    assert "n=100" in card, "el porcentaje sin su n no se puede juzgar"


@pytest.mark.parametrize("claude,gemini", [(1, 0), (0, 1)])
def test_una_sola_llamada_ya_es_un_porcentaje(claude, gemini):
    card = _card_uso_ia(_render(claude=claude, gemini=gemini))
    assert "Sin llamadas" not in card
    assert "n=1" in card


def test_el_costo_del_dia_va_con_dos_decimales():
    """Convivían %.2f, %.4f y %.6f en la misma pantalla."""
    html = _render(claude=5, gemini=0)
    costos = re.search(r"Costo IA(.*?)Costo Twilio", html, re.S)
    assert costos, "no se encontró la card «Costo IA»"
    montos = re.findall(r"\$(\d+\.(\d+))", costos.group(1))
    assert montos, "la card de costo no imprimió ningún monto"
    largos = {len(dec) for _, dec in montos}
    assert largos == {2}, (
        f"la card de costo mezcla precisiones: {sorted(largos)} decimales "
        f"en {[m for m, _ in montos]}"
    )

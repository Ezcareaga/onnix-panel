"""Unit tests for the path-(b) deterministic name-ask state counter.

POLISH-02 path-(b): replaces the LLM-judged name-ask counting with a
CODE-computed integer (count_name_ask_attempts) + a per-turn directive
section (build_name_attempts_section) injected at threshold >= 2.

Both functions are pure (no DB, no I/O, no Claude) and idempotent on
identical input. The counter is grounded in the REAL recepcionista
name-ask phrasings from the prompts.py few-shots (Ej 1/2/3/5/7/8/9).
"""
from __future__ import annotations

from app.bot.core.name_gate import (
    build_name_attempts_section,
    count_name_ask_attempts,
)
from app.bot.core.types import HistoryMessage


def _bot(body: str) -> HistoryMessage:
    return HistoryMessage(direction="outbound", sender_type="bot", body=body)


def _user(body: str) -> HistoryMessage:
    return HistoryMessage(direction="inbound", sender_type="contact", body=body)


# --------------------------------------------------------------------------
# count_name_ask_attempts
# --------------------------------------------------------------------------

def test_empty_history_returns_zero():
    assert count_name_ask_attempts([]) == 0


def test_none_history_returns_zero():
    assert count_name_ask_attempts(None) == 0


def test_single_bot_name_ask_returns_one():
    history = [_bot("Hola! Soy Onnix. ¿Con quién tengo el gusto?")]
    assert count_name_ask_attempts(history) == 1


def test_two_bot_name_asks_interleaved_with_user_turns_returns_two():
    history = [
        _bot("Hola! Soy Onnix. ¿Con quién tengo el gusto?"),
        _user("para comprar, con patio"),
        _bot("Anotado. ¿Tu nombre para que el asesor te ubique?"),
        _user("no importa el nombre, quiero ver opciones"),
    ]
    assert count_name_ask_attempts(history) == 2


def test_three_bot_name_asks_returns_three():
    history = [
        _bot("¿Con quién tengo el gusto?"),
        _user("hola"),
        _bot("¿Cómo te llamás?"),
        _user("..."),
        _bot("¿Tu nombre, para que te ubiquen?"),
    ]
    assert count_name_ask_attempts(history) == 3


def test_bot_non_name_ask_returns_zero():
    history = [
        _bot("Le paso tus datos a un asesor para el precio final."),
        _user("dale"),
        _bot("Anotado: casa en Lambaré hasta 150 mil."),
    ]
    assert count_name_ask_attempts(history) == 0


def test_user_turn_with_tu_nombre_not_counted():
    history = [
        _user("no importa mi nombre, mostrame opciones"),
        _user("tu nombre no me interesa darlo"),
    ]
    assert count_name_ask_attempts(history) == 0


def test_accent_insensitive_como_te_llamas_counted():
    # No accents — must still match "cómo te llamás".
    history = [_bot("hola, como te llamas?")]
    assert count_name_ask_attempts(history) == 1


def test_phrasing_como_te_llamas_with_accents():
    history = [_bot("¿Cómo te llamás y qué estás buscando?")]
    assert count_name_ask_attempts(history) == 1


def test_phrasing_con_quien_hablo():
    history = [_bot("Perfecto, ¿con quién hablo?")]
    assert count_name_ask_attempts(history) == 1


def test_phrasing_tu_nombre_para_que_te_ubiquen():
    history = [_bot("Por supuesto. ¿Tu nombre, para que te ubiquen?")]
    assert count_name_ask_attempts(history) == 1


def test_bare_quien_does_not_match():
    history = [_bot("¿Quién sabe? Te muestro las opciones disponibles.")]
    assert count_name_ask_attempts(history) == 0


def test_ordinary_prose_le_paso_datos_does_not_match():
    history = [_bot("Le paso tus datos a un asesor. Te contactan cuando puedan.")]
    assert count_name_ask_attempts(history) == 0


def test_empty_body_bot_turn_skipped():
    history = [
        _bot(""),
        _bot("¿Con quién tengo el gusto?"),
    ]
    assert count_name_ask_attempts(history) == 1


def test_counts_once_per_message_not_per_pattern():
    # A single message matching multiple patterns counts as ONE.
    history = [_bot("¿Cómo te llamás? ¿Tu nombre? ¿Con quién tengo el gusto?")]
    assert count_name_ask_attempts(history) == 1


def test_outbound_without_bot_sender_type_still_counts():
    # Bot turn = sender_type == "bot" OR direction == "outbound".
    msg = HistoryMessage(direction="outbound", sender_type="agent",
                         body="¿Con quién tengo el gusto?")
    assert count_name_ask_attempts([msg]) == 1


def test_idempotent_on_replay():
    history = [
        _bot("¿Con quién tengo el gusto?"),
        _user("hola"),
        _bot("¿Cómo te llamás?"),
    ]
    first = count_name_ask_attempts(history)
    second = count_name_ask_attempts(history)
    assert first == second == 2


# --------------------------------------------------------------------------
# build_name_attempts_section
# --------------------------------------------------------------------------

def test_section_zero_returns_empty():
    assert build_name_attempts_section(0) == ""


def test_section_negative_returns_empty():
    assert build_name_attempts_section(-1) == ""


def test_section_one_is_soft_note():
    out = build_name_attempts_section(1)
    assert out != ""
    assert "1" in out
    # SOFT: name desirable, NOT obligatory; no HARD imperative tokens.
    lower = out.lower()
    assert "deseable" in lower or "no obligatorio" in lower
    assert "register_lead" not in lower
    assert "captura parcial" not in lower


def test_section_two_is_hard_imperative():
    out = build_name_attempts_section(2)
    assert "2" in out
    assert "register_lead" in out
    assert "captura parcial" in out.lower()
    # explicit NO + nombre instruction
    assert "NO" in out
    assert "nombre" in out.lower()


def test_section_three_is_hard_imperative_with_exact_count():
    out = build_name_attempts_section(3)
    assert "3" in out
    assert "register_lead" in out
    assert "captura parcial" in out.lower()
    assert "NO" in out
    assert "nombre" in out.lower()


def test_section_hard_names_exact_count():
    # The exact integer must appear (deterministic state).
    out = build_name_attempts_section(5)
    assert "5" in out


def test_section_is_idempotent():
    assert build_name_attempts_section(2) == build_name_attempts_section(2)


# --------------------------------------------------------------------------
# Iteración 3 (forced derivation) — count_bot_turns / forced_derivation_due /
# build_forced_lead_motivo
#
# Root cause 124.4: conv 168 proved Haiku does not reliably honor the HARD
# directive (model non-compliance) and conv 206 proved the name-ask signal
# never reaches 2 in the criteria-loop shape. The derivation guarantee moves
# to CODE: a deterministic threshold computed from history that the
# orchestrator enforces post-AI.
# --------------------------------------------------------------------------

from app.bot.core.name_gate import (  # noqa: E402
    FORCED_DERIVATION_NOTE,
    build_forced_lead_motivo,
    count_bot_turns,
    forced_derivation_due,
)


def test_count_bot_turns_empty_and_none():
    assert count_bot_turns([]) == 0
    assert count_bot_turns(None) == 0


def test_count_bot_turns_counts_only_bot_messages():
    history = [
        _user("Hola"),
        _bot("Hola! Soy Onnix. ¿Con quién tengo el gusto y qué estás buscando?"),
        _user("Buscar propiedad"),
        _bot("¿Qué tipo de propiedad buscás, en qué zona y presupuesto?"),
        _user("Venta, J Augusto Saldívar"),
        _bot("¿Qué tipo de propiedad te interesa y cuál es tu presupuesto?"),
    ]
    assert count_bot_turns(history) == 3


def test_count_bot_turns_skips_empty_bodies():
    history = [_bot(""), _bot("Hola!"), _user("hola")]
    assert count_bot_turns(history) == 1


def test_forced_derivation_due_two_name_asks_fires():
    # Conv-168 shape: explicit name evasion (>=2 asks).
    history = [
        _bot("¿Con quién tengo el gusto?"),
        _user("quiero un depto en el centro"),
        _bot("¿Tu nombre para que el asesor te ubique?"),
        _user("500 usd"),
    ]
    assert forced_derivation_due(history) is True


def test_forced_derivation_due_three_bot_turns_one_ask_fires():
    # Conv-206 shape: 1 name-ask then a criteria-gathering loop. The bot has
    # had 3 full turns without capturing a name -> derive.
    history = [
        _bot("Hola! ¿Con quién tengo el gusto y qué estás buscando?"),
        _user("Buscar propiedad"),
        _bot("¿Qué tipo, zona y presupuesto?"),
        _user("Venta, J Augusto Saldívar"),
        _bot("¿Qué tipo de propiedad y presupuesto?"),
        _user("Más información"),
    ]
    assert count_name_ask_attempts(history) == 1  # the signal path-(b) missed
    assert forced_derivation_due(history) is True


def test_forced_derivation_due_below_both_thresholds_does_not_fire():
    history = [
        _bot("Hola! ¿Con quién tengo el gusto?"),
        _user("hola"),
        _bot("¿Qué estás buscando?"),
    ]
    assert forced_derivation_due(history) is False


def test_forced_derivation_due_empty_history_does_not_fire():
    assert forced_derivation_due([]) is False
    assert forced_derivation_due(None) is False


def test_build_forced_lead_motivo_includes_criteria():
    motivo = build_forced_lead_motivo(
        {"operacion": "alquiler", "tipo": "departamento", "ciudad": "Asunción",
         "precio_max": 500},
    )
    low = motivo.lower()
    assert "derivación automática" in low or "derivacion automatica" in low
    assert "alquiler" in low
    assert "departamento" in low
    assert "500" in motivo


def test_build_forced_lead_motivo_empty_filtros_still_nonempty():
    motivo = build_forced_lead_motivo({})
    assert motivo.strip()
    low = motivo.lower()
    assert "derivación automática" in low or "derivacion automatica" in low


def test_forced_derivation_note_is_nonempty_constant():
    assert isinstance(FORCED_DERIVATION_NOTE, str)
    assert "asesor" in FORCED_DERIVATION_NOTE.lower()

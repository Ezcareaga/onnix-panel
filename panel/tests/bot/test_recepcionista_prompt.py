"""Tests for the recepcionista system prompt (M6.3 Plan 123-04).

Covers BOT-10/BOT-11 + the flow framing every Wave-3 plan depends on:

  - Onnix frames herself as a RECEPTIONIST (recibir -> capturar -> derivar),
    not a property searcher.
  - get_system_prompt(mode='recepcionista') returns RECEPCIONISTA_SYSTEM_PROMPT;
    get_system_prompt() / mode='busqueda' returns the unchanged buscador prompt
    (byte-identical — zero busqueda/TG regression).
  - The Gemini variant mirrors the same mode behavior.
  - The orchestrator selects the prompt by the resolved per-turn mode.
  - BOT-12 unit: name+interest derivation transitions the contact to
    'interested'; a greeting/name-only turn stays 'bot_replied'.

All dependencies mocked — no real DB or API calls.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.ai.prompts import (
    RECEPCIONISTA_SYSTEM_PROMPT,
    SYSTEM_PROMPT_TEMPLATE,
    get_gemini_system_prompt,
    get_system_prompt,
)
from app.bot.core.orchestrator import Orchestrator
from app.bot.core.types import (
    BotRequest,
    ContactInfo,
    ConversationInfo,
    ConversationState,
)


# ===========================================================================
# BOT-11: Onnix identity + receptionist framing
# ===========================================================================

def test_bot_se_identifica_como_bot():
    """RECEPCIONISTA_SYSTEM_PROMPT frames Onnix as a receptionist (recibir ->
    capturar -> derivar), not as a property searcher."""
    prompt = RECEPCIONISTA_SYSTEM_PROMPT

    # Onnix identity preserved.
    assert "Onnix" in prompt

    # Receptionist framing: recibir / capturar / derivar.
    low = prompt.lower()
    assert "recibir" in low
    assert "captur" in low  # capturar / captura
    assert "deriv" in low   # derivar / derivación

    # NOT a property searcher: must not carry the buscador objective.
    assert "encontrar propiedades" not in low

    # The buscador objective string lives in the buscador prompt — sanity check
    # the two prompts are genuinely different documents.
    assert prompt != SYSTEM_PROMPT_TEMPLATE


def test_recepcionista_prompt_covers_milestones_and_guard():
    """The prompt instructs the 4 flow milestones, bulk-capture, LEAD_REF,
    origin-aware greeting, resistente path and the switch-guard A/B/C block."""
    prompt = RECEPCIONISTA_SYSTEM_PROMPT
    low = prompt.lower()

    # 4 flow milestones framing.
    assert "saludo" in low
    assert "nombre" in low
    assert "inter" in low      # interés / interes
    assert "register_lead" in low

    # Origin-aware greeting (directo / indirecto / sin contexto).
    assert "directo" in low
    assert "indirecto" in low
    assert "sin contexto" in low

    # Bulk capture instruction.
    assert "bulk" in low

    # LEAD_REF derivation contract.
    assert "LEAD_REF" in prompt or "lead_ref" in low
    assert "lead-" in low      # LEAD-{contact_id} format

    # Resistente / defensive path.
    assert "resistente" in low

    # Switch-guard block: the three classes A/B/C.
    assert "switch" in low
    assert "preguntar" in low
    assert "no switch" in low

    # Agendar visita guard.
    assert "agendar_visita" in low


def test_recepcionista_reuses_shared_blocks():
    """The recepcionista prompt reuses the shared Identidad/Personalidad rules
    verbatim (paraguayan tuteo, no laughter, no time promises, no tech)."""
    prompt = RECEPCIONISTA_SYSTEM_PROMPT
    # Identidad core line (no finjas ser humano).
    assert "NO finjas ser humano" in prompt
    # Personalidad: tuteo paraguayo + no laughter.
    assert "tuteo paraguayo" in prompt
    assert "NUNCA uses risas" in prompt


# ===========================================================================
# M6.3.1 POLISH-01: name is nice-to-have, NOT a hard gate for register_lead
# ===========================================================================

def test_polish01_name_is_not_a_hard_gate():
    """POLISH-01: the hard name+interest gate is removed; deriving with interest
    alone (or criteria-only) is allowed. The prompt must drop the old gate phrase
    and make the name explicitly optional."""
    low = RECEPCIONISTA_SYSTEM_PROMPT.lower()

    # The removed hard-gate phrase must be gone.
    assert "cuando tengas nombre + interés" not in low

    # The name must be framed as optional / nice-to-have.
    assert any(
        phrase in low
        for phrase in ("deseable", "con o sin nombre", "no obligatorio")
    ), "expected name framed as optional (deseable / con o sin nombre / no obligatorio)"


# ===========================================================================
# M6.3.1 POLISH-02: countable evasion threshold (path a, narrative only)
# ===========================================================================

def test_polish02_countable_evasion_threshold():
    """POLISH-02: the resistente block carries a countable rule — ask the name at
    most twice, NEVER a 3rd time, then fire register_lead with captura parcial.
    Narrative only: no state counter, no code asserted here."""
    prompt = RECEPCIONISTA_SYSTEM_PROMPT

    # Countable trigger co-occurs with register_lead in the prompt.
    assert "2 veces" in prompt
    assert "register_lead" in prompt

    # The "never a 3rd time" hard rule.
    assert any(marker in prompt for marker in ("3ª", "tercera")), (
        "expected the 'NUNCA … una 3ª vez' rule (3ª / tercera)"
    )


# ===========================================================================
# M6.3.1 POLISH-03: defensive-capture few-shots Ej 7/8/9
# ===========================================================================

def test_polish03_defensive_capture_few_shots():
    """POLISH-03: three new defensive few-shots (Ej 7/8/9) teach deriving WITHOUT
    a name via captura parcial, covering the three real-corpus shapes
    (search-shopper / criteria-only, hablar-con-asesor, IC-URL paste)."""
    prompt = RECEPCIONISTA_SYSTEM_PROMPT
    low = prompt.lower()

    # Ej 5 already has one; Ej 7/8/9 add three more -> >= 4 total.
    assert prompt.count("[register_lead con captura parcial]") >= 4

    # The three real-corpus shape markers.
    assert "Lambaré" in prompt          # search-shopper / criteria-only (492/206)
    assert "Hablar con asesor" in prompt  # 355
    assert "infocasas" in low           # IC-URL paste (168)


# ===========================================================================
# Mode selection in the prompt builders
# ===========================================================================

def test_get_system_prompt_default_is_buscador():
    """get_system_prompt() with no mode returns the unchanged buscador prompt."""
    assert get_system_prompt() == SYSTEM_PROMPT_TEMPLATE


def test_get_system_prompt_busqueda_is_buscador_byte_identical():
    """mode='busqueda' returns the buscador prompt byte-identical to before."""
    assert get_system_prompt(mode="busqueda") == SYSTEM_PROMPT_TEMPLATE
    # Byte-identity proof: default == busqueda == the untouched template.
    assert get_system_prompt() == get_system_prompt(mode="busqueda")


def test_get_system_prompt_recepcionista_returns_recepcionista():
    """mode='recepcionista' returns RECEPCIONISTA_SYSTEM_PROMPT (≠ buscador)."""
    assert get_system_prompt(mode="recepcionista") == RECEPCIONISTA_SYSTEM_PROMPT
    assert get_system_prompt(mode="recepcionista") != get_system_prompt(mode="busqueda")


def test_get_gemini_system_prompt_mirrors_mode():
    """The Gemini variant mirrors mode behavior: recepcionista base for
    recepcionista, buscador base for busqueda; both keep the 'no tools' addendum.
    """
    gemini_busqueda = get_gemini_system_prompt(mode="busqueda")
    gemini_recep = get_gemini_system_prompt(mode="recepcionista")

    # Default == busqueda (byte-identical).
    assert get_gemini_system_prompt() == gemini_busqueda

    # Each builds on the matching base.
    assert gemini_busqueda.startswith(SYSTEM_PROMPT_TEMPLATE)
    assert gemini_recep.startswith(RECEPCIONISTA_SYSTEM_PROMPT)

    # Both carry the shared 'no tools' addendum.
    assert "Limitaciones actuales" in gemini_busqueda
    assert "Limitaciones actuales" in gemini_recep

    # The two variants differ (different base).
    assert gemini_recep != gemini_busqueda


# ===========================================================================
# Orchestrator threads the resolved mode into the per-turn AI call
# ===========================================================================

def _make_orchestrator():
    claude = AsyncMock()
    gemini = AsyncMock()
    circuit_breaker = MagicMock()
    circuit_breaker.is_open = False
    search_service = AsyncMock()
    conversation_manager = AsyncMock()
    # sync methods on ConversationManager — override AsyncMock defaults
    conversation_manager.check_human_cooldown = MagicMock(return_value=False)
    conversation_manager.tick_pending_alternatives_ttl = MagicMock()
    response_builder = MagicMock()
    tool_executor = AsyncMock()
    tool_executor.build_tool_result_message = MagicMock()
    orch = Orchestrator(
        claude=claude,
        gemini=gemini,
        circuit_breaker=circuit_breaker,
        search_service=search_service,
        conversation_manager=conversation_manager,
        response_builder=response_builder,
        tool_executor=tool_executor,
    )
    return orch, {
        "conversation_manager": conversation_manager,
    }


def _wa_request():
    return BotRequest(
        platform="whatsapp", chat_id="595981000000", user_id="595981000000",
        user_name="Tester", text="hola", external_id="m1",
    )


def _tg_request():
    return BotRequest(
        platform="telegram", chat_id="12345", user_id="12345",
        user_name="Tester", text="busco casa en Asuncion", external_id="m1",
    )


def _contact(status="new", source=None, infocasas_ref=None, platform="whatsapp"):
    return ContactInfo(
        id=1, name="Tester", status=status, platform=platform,
        source=source, infocasas_ref=infocasas_ref,
    )


def _conversation(platform="whatsapp"):
    return ConversationInfo(
        id=10, contact_id=1, platform=platform, chat_id="x",
        is_bot_active=True,
    )


def _setup_flow(mocks, contact, conversation):
    cm = mocks["conversation_manager"]
    cm.resolve_contact.return_value = contact
    cm.get_or_create_conversation.return_value = conversation
    cm.check_human_cooldown.return_value = False
    cm.get_history.return_value = []
    cm.get_search_context.return_value = ConversationState()


async def _run_capturing_system_prompt(orch, request):
    """Run handle_message with run_ai_with_fallback patched to capture the
    system_prompt kwarg, short-circuiting by returning a BotResponse."""
    from app.bot.core.types import BotResponse

    captured = {}

    async def _fake_run_ai(*args, **kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt")
        captured["gemini_system_prompt"] = kwargs.get("gemini_system_prompt")
        return BotResponse(text="ok", intent="conversacion")

    with patch(
        "app.bot.core.orchestrator.run_ai_with_fallback",
        new=AsyncMock(side_effect=_fake_run_ai),
    ), patch(
        "app.bot.core.orchestrator.check_bot_active_locked",
        new=AsyncMock(return_value=True),
    ), patch(
        "app.bot.core.orchestrator.try_shortcut_dispatch",
        new=AsyncMock(return_value=None),
    ):
        await orch.handle_message(request, AsyncMock())
    return captured


@pytest.mark.asyncio
async def test_recepcionista_mode_selects_recepcionista_prompt():
    """A whatsapp turn that resolves to recepcionista passes the recepcionista
    prompt into the AI call."""
    orch, mocks = _make_orchestrator()
    # source='vista_publica' -> _resolve_mode auto-detects recepcionista.
    _setup_flow(mocks, _contact(source="vista_publica"), _conversation())

    with patch(
        "app.bot.core.orchestrator.bot_setting_repo.get_value",
        new=AsyncMock(return_value="busqueda"),
    ):
        captured = await _run_capturing_system_prompt(orch, _wa_request())

    assert captured["system_prompt"] == RECEPCIONISTA_SYSTEM_PROMPT
    assert captured["gemini_system_prompt"] == get_gemini_system_prompt(
        mode="recepcionista"
    )


@pytest.mark.asyncio
async def test_busqueda_mode_uses_buscador_prompt():
    """A busqueda turn passes the unchanged buscador prompt into the AI call."""
    orch, mocks = _make_orchestrator()
    # No auto-detect signal, default busqueda.
    _setup_flow(mocks, _contact(), _conversation())

    with patch(
        "app.bot.core.orchestrator.bot_setting_repo.get_value",
        new=AsyncMock(return_value="busqueda"),
    ):
        captured = await _run_capturing_system_prompt(orch, _wa_request())

    assert captured["system_prompt"] == SYSTEM_PROMPT_TEMPLATE
    assert captured["gemini_system_prompt"] == get_gemini_system_prompt(
        mode="busqueda"
    )


@pytest.mark.asyncio
async def test_telegram_always_uses_buscador_prompt():
    """D-2: telegram never enters recepcionista, even with auto-detect signals."""
    orch, mocks = _make_orchestrator()
    _setup_flow(
        mocks,
        _contact(source="vista_publica", infocasas_ref="IC-1", platform="telegram"),
        _conversation(platform="telegram"),
    )

    with patch(
        "app.bot.core.orchestrator.bot_setting_repo.get_value",
        new=AsyncMock(return_value="recepcionista"),
    ):
        captured = await _run_capturing_system_prompt(orch, _tg_request())

    assert captured["system_prompt"] == SYSTEM_PROMPT_TEMPLATE


# ===========================================================================
# BOT-12: status transition rule (unit-level on the persistence path)
# ===========================================================================

@pytest.mark.asyncio
async def test_status_interested_on_name_plus_interest():
    """When derivation runs (name + interest captured -> register_lead), the
    persistence path transitions the contact to status='interested'. A turn
    that only greeted / captured a name (no register_lead) stays 'bot_replied'.
    """
    from app.bot.handlers.lead_persist import persist_lead_outcome

    # --- derivation: register_lead fired -> persist_lead_outcome -> interested
    contact = _contact(status="bot_replied")
    ctx = ConversationState()
    session = AsyncMock()
    claude = AsyncMock()
    claude.send_message.return_value = MagicMock(text='{"perfil": "x"}')

    await persist_lead_outcome(
        session, contact, _wa_request(), [], ctx,
        "Tester quiere precio final y agendar visita",
        claude_client=claude,
    )

    sql_texts = [
        getattr(c[0][0], "text", str(c[0][0])) if c[0] else ""
        for c in session.execute.call_args_list
    ]
    interested = [s for s in sql_texts if "status = 'interested'" in s]
    assert interested, f"Expected status='interested' UPDATE, got: {sql_texts}"
    # Guarded transition: only advances from pre-derivation statuses.
    assert "status IN ('new', 'contacted', 'bot_replied', 'agent_replied')" in interested[0]

    # --- greeting/name-only turn (no lead): handle_message advances new->bot_replied
    orch, mocks = _make_orchestrator()
    _setup_flow(mocks, _contact(status="new", source="vista_publica"), _conversation())
    session2 = AsyncMock()

    with patch(
        "app.bot.core.orchestrator.bot_setting_repo.get_value",
        new=AsyncMock(return_value="busqueda"),
    ), patch(
        "app.bot.core.orchestrator.check_bot_active_locked",
        new=AsyncMock(return_value=True),
    ), patch(
        "app.bot.core.orchestrator.try_shortcut_dispatch",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.bot.core.orchestrator.run_ai_with_fallback",
        new=AsyncMock(return_value=_no_lead_outcome()),
    ):
        await orch.handle_message(_wa_request(), session2)

    sql2 = [
        getattr(c[0][0], "text", str(c[0][0])) if c[0] else ""
        for c in session2.execute.call_args_list
    ]
    assert any("status = 'bot_replied'" in s for s in sql2), (
        f"greeting/name-only turn should advance new->bot_replied, got: {sql2}"
    )
    # No premature 'interested' on a non-lead turn.
    assert not any("status = 'interested'" in s for s in sql2)


def _no_lead_outcome():
    """Build a minimal AIOutcome with no lead/detail/opt-out (text-only turn)."""
    from app.bot.ai.ai_dispatch import AIOutcome
    from app.bot.ai.types import AIResponse

    ai_response = AIResponse(
        text="Hola! Soy Onnix. ¿Cómo te llamás?",
        tool_calls=[],
        model="claude-haiku",
        input_tokens=10,
        output_tokens=10,
        stop_reason="end_turn",
        raw_content=[],
    )
    return AIOutcome(
        ai_response=ai_response,
        properties_collected=[],
        all_ids_collected=[],
        is_lead=False,
        is_detail=False,
        is_opt_out=False,
        lead_motivo="",
        events_to_record=[],
        tool_iterations=0,
        fallback_used=False,
    )

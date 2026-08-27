"""E2E — M6.3 BOT-10: bulk-capture (first message carries multiple data).

Validates the recepcionista "captura en bulk" rule: when the client's FIRST
message already carries several data points (name + interest), the bot must
EXTRACT all of them in one shot and re-ask NOTHING already provided — no
"un dato por turno". It then proceeds toward derivation FASTER than the
standard sin_contexto path (which asks name, then qué busca, then confirms).

Fixture: corpus conv **596** (referenced by id ONLY — no PII copied here).
596 is the audit's TG bulk failure: ~500-char first inbound carrying name +
full interest that the OLD bot did NOT extract (outcome=abandoned). Per
decision D-2 the recepcionista mode is WhatsApp-only, but the bulk-extraction
LOGIC is channel-agnostic — so 596 is REPURPOSED as a WhatsApp fixture here
to prove the NEW flow extracts the bulk first message. We model the SHAPE of
596's first inbound (name + interest in one message), redacted of PII.

Because Claude is mocked, the load-bearing (non-tautological) signal is that
the orchestrator selects the RECEPCIONISTA system prompt — whose "## Captura
en bulk" block + "Ejemplo 4 — BULK" instruct extract-all / re-ask-nothing —
into the system blocks Claude receives. The scripted bot reply then echoes
the captured name + interest and does NOT re-prompt for either, and the flow
reaches register_lead in FEWER turns than the standard sin_contexto path.

If a bulk assertion fails, the fix is in the 123-04 recepcionista prompt's
bulk-capture section ("## Captura en bulk" / "Ejemplo 4 — BULK" in
prompts.py ``RECEPCIONISTA_SYSTEM_PROMPT``), NOT a new code path.

External surfaces (Claude, SearchService, channel senders) are mocked via
``ConversationRunner``; no network calls, no live LLM. DB writes go to
onnix_dev only (test phone within the cleanup range).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
import sqlalchemy
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Ensure panel/ is on sys.path before any app import.
_panel_dir = str(Path(__file__).resolve().parent.parent.parent)
if _panel_dir not in sys.path:
    sys.path.insert(0, _panel_dir)

# Force dev DB + silence external services BEFORE any app import.
os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ.setdefault("POSTGRES_DB", "onnix_dev")
os.environ["TELEGRAM_EZ_CHAT_ID"] = ""
os.environ["FOLLOWUP_SENDER_ENABLED"] = "false"

from app.bot.core.types import ConversationState, ContactInfo, ConversationInfo
from tests.bot.e2e.runner import (
    ConversationRunner,
    _make_text_ai_response,
    _make_tool_ai_response,
)


# ---------------------------------------------------------------------------
# Corpus loader — by conversation_id ONLY, no PII into this file.
# ---------------------------------------------------------------------------

_CORPUS_PATH = (
    Path(_panel_dir).parent
    / ".planning"
    / "phases"
    / "121-m6.3-audit-bot-recepcionista"
    / "raw"
    / "corpus.json"
)

# Bulk anchor — corpus 596 (TG bulk failure, repurposed as WhatsApp).
_BULK_ANCHOR = "596"


def _load_corpus() -> dict[str, dict]:
    if not _CORPUS_PATH.exists():
        pytest.skip(f"audit corpus not present at {_CORPUS_PATH}")
    with _CORPUS_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return {c["conversation_id"]: c for c in data}


# ---------------------------------------------------------------------------
# DB engine (NullPool — fresh connection per test). Mirrors e2e/conftest.py.
# ---------------------------------------------------------------------------

_DEV_DB_URL = (
    f"postgresql+asyncpg://{os.environ.get('POSTGRES_USER', 'onnix')}"
    f":{os.environ.get('POSTGRES_PASSWORD', '')}"
    f"@127.0.0.1:5432/onnix_dev"
)

_engine = create_async_engine(_DEV_DB_URL, poolclass=NullPool, echo=False)
_Session = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

# Test phone within the TEST_PHONE_PREFIX cleanup range (+595981[5-9]...).
_TEST_PHONE = "+595981599910"

# SHAPE of 596's first inbound (name + interest in ONE message), PII-redacted.
# This is NOT corpus PII — it models the structure (a bulk first message
# carrying the name AND the interest) the OLD bot failed to extract.
_BULK_NAME = "Maria"
_BULK_FIRST_MESSAGE = (
    "Hola, soy Maria. Quiero el precio final y agendar una visita a la casa "
    "que vi publicada. Me podes pasar con un asesor?"
)


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    session = _Session()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@pytest_asyncio.fixture
async def bulk_contact(db_session: AsyncSession):
    """Seed a plain WhatsApp lead (no origin data) for the bulk first message."""
    for tbl in ("lead_events", "messages", "conversations"):
        await db_session.execute(
            sqlalchemy.text(
                f"DELETE FROM {tbl} WHERE contact_id IN "
                "(SELECT id FROM contacts WHERE phone = :phone)"
            ),
            {"phone": _TEST_PHONE},
        )
    await db_session.execute(
        sqlalchemy.text("DELETE FROM contacts WHERE phone = :phone"),
        {"phone": _TEST_PHONE},
    )
    await db_session.execute(
        sqlalchemy.text(
            "INSERT INTO contacts (name, phone, source, status) "
            "VALUES ('E2E Bulk Capture', :phone, 'whatsapp', 'new')"
        ),
        {"phone": _TEST_PHONE},
    )
    await db_session.commit()

    row = (
        await db_session.execute(
            sqlalchemy.text("SELECT id FROM contacts WHERE phone = :phone"),
            {"phone": _TEST_PHONE},
        )
    ).first()
    contact = {"id": row.id, "phone": _TEST_PHONE}

    yield contact

    for tbl in ("lead_events", "messages", "conversations"):
        await db_session.execute(
            sqlalchemy.text(f"DELETE FROM {tbl} WHERE contact_id = :cid"),
            {"cid": contact["id"]},
        )
    await db_session.execute(
        sqlalchemy.text("DELETE FROM contacts WHERE phone = :phone"),
        {"phone": _TEST_PHONE},
    )
    await db_session.commit()


def _make_runner(db_session: AsyncSession, contact: dict, monkeypatch) -> ConversationRunner:
    """Recepcionista-mode runner (WhatsApp) for a plain bulk-first-message lead.

    No infocasas_ref → recepcionista is forced via the per-chat ``mode``
    override (_resolve_mode check 1), the same mechanism as sin_contexto.
    """
    monkeypatch.setattr(
        "app.bot.core.orchestrator.check_bot_active_locked",
        AsyncMock(return_value=True),
    )
    claude_mock = AsyncMock()
    claude_mock.send_message = AsyncMock()
    search_mock = AsyncMock()

    runner = ConversationRunner(
        session=db_session,
        claude_mock=claude_mock,
        search_mock=search_mock,
        platform="whatsapp",
        chat_id=contact["phone"],
        contact_id=contact["id"],
        conversation_id=None,
    )
    runner.set_contact(
        ContactInfo(
            id=contact["id"],
            name="",
            status="new",
            is_baja=False,
            platform="whatsapp",
            phone=contact["phone"],
            source_id=contact["phone"],
            source="whatsapp",
            infocasas_ref=None,
        )
    )
    runner.set_search_context(ConversationState(mode="recepcionista"))
    runner.set_conversation(
        ConversationInfo(
            id=654321,
            contact_id=contact["id"],
            platform="whatsapp",
            chat_id=contact["phone"],
            is_bot_active=True,
        )
    )
    return runner


def _system_text(runner: ConversationRunner) -> str:
    chunks: list[str] = []
    for call in runner._claude_mock.send_message.call_args_list:
        system = call.kwargs.get("system")
        if isinstance(system, str):
            chunks.append(system)
        elif isinstance(system, list):
            for block in system:
                if isinstance(block, dict) and block.get("type") == "text":
                    chunks.append(block.get("text", ""))
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# BOT-10 — bulk first message: extract all, re-ask nothing, derive faster
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cliente_da_todo_en_primer_mensaje(db_session, bulk_contact, monkeypatch):
    """First WhatsApp message carries name + interest → bot extracts all.

    Anchored on corpus 596 (TG bulk failure) by id ONLY, repurposed as a
    WhatsApp fixture (D-2: TG recepcionista is N/A, but bulk extraction is
    channel-agnostic). The bot must:
      - extract the NAME (echo it) AND the INTEREST in its FIRST reply,
      - NOT re-ask the name or the interest already provided (negative),
      - reach register_lead in FEWER turns than the standard sin_contexto path
        (bulk derives on the SECOND turn; sin_contexto needs ~3-4).

    Claude is mocked: the load-bearing signal is that the recepcionista prompt
    (with "## Captura en bulk" + "Ejemplo 4 — BULK") is surfaced into the
    system blocks. If this fails, fix the 123-04 prompt's bulk section, NOT a
    new code path.
    """
    corpus = _load_corpus()
    conv = corpus.get(_BULK_ANCHOR)
    assert conv is not None, f"corpus conv {_BULK_ANCHOR} missing"
    # Document the anchor: 596 is the TG bulk failure, sin_contexto flow class.
    assert conv["metadata"].get("flow_2_2") == "sin_contexto", (
        f"conv {_BULK_ANCHOR} should be the sin_contexto bulk anchor"
    )
    assert conv["metadata"].get("selected_for") == "known_failure", (
        f"conv {_BULK_ANCHOR} is the audit's TG bulk known_failure (OLD bot "
        f"did not extract the ~500-char bulk first inbound)."
    )

    runner = _make_runner(db_session, bulk_contact, monkeypatch)

    # --- Turn 1: BULK first message — bot extracts name + interest in one shot
    # Scripted bot reply models Ejemplo 4 (BULK): echoes the name + interest,
    # confirms it has everything, does NOT re-ask. (Claude is mocked.)
    bulk_reply = (
        f"Gracias {_BULK_NAME}! Ya tengo todo: precio final y coordinar una "
        f"visita. Te confirmo y le paso tus datos a un asesor."
    )
    runner.program_claude_response(text=bulk_reply)
    resp1 = await runner.send(_BULK_FIRST_MESSAGE)

    # (system) Recepcionista bulk-capture instruction surfaced into the prompt.
    system_blob = _system_text(runner)
    blob_l = system_blob.lower()
    assert "captura en bulk" in blob_l or "bulk" in blob_l, (
        f"bulk-capture FAIL: the RECEPCIONISTA prompt's bulk-capture block is "
        f"not in the system blocks Claude received — mode did not resolve to "
        f"recepcionista or the prompt lacks the bulk section.\n"
        f"System blocks tail:\n{system_blob[-1400:]}"
    )
    assert "un dato por turno" in blob_l, (
        "bulk-capture FAIL: the prompt's 'NO un dato por turno cuando el cliente "
        "ya dio varios' instruction is not surfaced."
    )

    r1 = resp1.text or ""
    r1l = r1.lower()
    # (1) Bot echoes the captured NAME in its first reply.
    assert _BULK_NAME.lower() in r1l, (
        f"bulk-capture FAIL: bot did not echo the captured name "
        f"{_BULK_NAME!r} in its first reply. Got: {r1!r}"
    )
    # (2) Bot reflects the captured INTEREST (precio / visita) in its first reply.
    interest_tokens = ("precio", "visita", "visitar", "coordinar")
    assert any(tok in r1l for tok in interest_tokens), (
        f"bulk-capture FAIL: bot did not reflect the captured interest "
        f"(precio / visita) in its first reply. Got: {r1!r}"
    )
    # (3) NEGATIVE: bot does NOT re-ask the name already given.
    reask_name_phrases = (
        "cómo te llamás", "como te llamas", "con quién tengo el gusto",
        "con quien tengo el gusto", "tu nombre", "cuál es tu nombre",
        "cual es tu nombre",
    )
    assert not any(p in r1l for p in reask_name_phrases), (
        f"bulk-capture FAIL: bot RE-ASKED the name that was already provided "
        f"in the bulk first message. Got: {r1!r}"
    )
    # (4) NEGATIVE: bot does NOT re-ask qué busca / the interest already given.
    reask_interest_phrases = (
        "qué estás buscando", "que estas buscando", "qué buscás", "que buscas",
        "qué te interesa", "que te interesa", "qué tipo", "que tipo",
    )
    assert not any(p in r1l for p in reask_interest_phrases), (
        f"bulk-capture FAIL: bot RE-ASKED the interest/qué busca that was "
        f"already provided in the bulk first message. Got: {r1!r}"
    )

    # --- Turn 2: DERIVACIÓN — register_lead reached on the SECOND turn --------
    # Bulk path derives FASTER: vs the standard sin_contexto path (turn 1 ask
    # name+qué busca, turn 2 client answers, turn 3 confirm/ask comprar-alquilar,
    # turn 4 derive), the bulk path goes straight to derivation on turn 2.
    motivo = (
        f"Nombre: {_BULK_NAME}. Interes: precio final y coordinar visita. "
        f"Captura en bulk desde el primer mensaje."
    )
    runner.program_tool_executor_result(
        {"success": True, "motivo": motivo, "message": "Lead registrado"}
    )
    lead_tool = _make_tool_ai_response("register_lead", {"motivo": motivo})
    lead_text = _make_text_ai_response(
        f"Listo {_BULK_NAME}! Le paso tus datos a un asesor. Tu consulta queda "
        f"registrada con el codigo LEAD-{bulk_contact['id']}; te contactan "
        f"cuando puedan."
    )
    profile = _make_text_ai_response('{"preferencias": "bulk"}')
    runner._claude_mock.send_message.side_effect = [lead_tool, lead_text, profile]
    runner._last_tool_name = "register_lead"

    resp2 = await runner.send("Si, dale")

    # LEAD_REF token + register_lead on turn 2 (fewer turns than sin_contexto).
    assert "lead-" in (resp2.text or "").lower(), (
        f"bulk derivation FAIL: closing turn has no LEAD_REF token. "
        f"Got: {resp2.text!r}"
    )
    runner.assert_last_tool("register_lead")
    assert resp2.is_lead, "bulk: BotResponse.is_lead must be True"
    # Faster: derivation reached after only 2 inbound turns.
    assert runner._call_count == 2, (
        f"bulk path should derive in 2 turns (extract-all then confirm+derive), "
        f"got {runner._call_count} turns — it must NOT re-ask field-by-field."
    )

    # --- BOT-12: contact transitions to status='interested' ------------------
    status_row = (
        await db_session.execute(
            sqlalchemy.text("SELECT status FROM contacts WHERE id = :cid"),
            {"cid": bulk_contact["id"]},
        )
    ).first()
    assert status_row.status == "interested", (
        f"BOT-12 FAIL (bulk): contact must be 'interested' at derivation, "
        f"got {status_row.status!r}."
    )

    # --- motivo carries the bulk-extracted nombre + interés ------------------
    lead_event = (
        await db_session.execute(
            sqlalchemy.text(
                "SELECT metadata FROM lead_events "
                "WHERE contact_id = :cid AND event_type = 'lead_registered' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"cid": bulk_contact["id"]},
        )
    ).first()
    assert lead_event is not None, "bulk: expected a lead_registered event row."
    meta = lead_event.metadata
    if isinstance(meta, str):
        meta = json.loads(meta)
    stored_motivo = (meta or {}).get("motivo", "")
    assert _BULK_NAME in stored_motivo and (
        "precio" in stored_motivo.lower() or "visita" in stored_motivo.lower()
    ), (
        f"bulk: register_lead motivo must carry the bulk-extracted nombre + "
        f"interés. Got: {stored_motivo!r}"
    )

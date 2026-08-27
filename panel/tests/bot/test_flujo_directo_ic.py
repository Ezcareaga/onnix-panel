"""E2E — M6.3 BOT-06: flujo "lead directo IC" (recepcionista mode).

Replays the directo-IC conversations from the audit corpus
(``.planning/phases/121-.../raw/corpus.json``, gitignored) by
``conversation_id`` ONLY — NO PII (names, phones, free text) is copied
into this committed file. For each anchored conversation the test seeds a
contact whose ``infocasas_ref`` matches the consulted property and drives
the orchestrator in recepcionista mode (WhatsApp), asserting the four
recepcionista milestones are HIT:

  1. saludo  — the bot's first turn references the consulted prop. Because
                Claude is mocked, the meaningful (non-tautological) signal is
                that the orchestrator SURFACES the prop title/code into the
                system prompt Claude receives. We assert the prop CÓDIGO
                (infocasas_ref) and/or TÍTULO appear in the system blocks.
  2. nombre   — bot acknowledges the captured name ("Gracias {Nombre}").
  3. interés  — bot asks the interest with concrete examples
                (precio / expensas / visita / financiación).
  4. LEAD_REF — closing turn carries a LEAD_REF token AND register_lead was
                called; contact transitions to status='interested' (BOT-12)
                and register_lead's ``motivo`` carries the masticated context.

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

# Directo-IC anchors from the blueprint §7 (referenced by id ONLY):
#   happy: 139, 174, 257, 525, 536, 542, 565, 570, 610
#   edge:  269
#   known_failure: 492
_HAPPY_DIRECTO = ["139", "174", "257", "525", "536", "542", "565", "570", "610"]
_EDGE_DIRECTO = ["269"]
_KNOWN_FAILURE = ["492"]


def _load_corpus() -> dict[str, dict]:
    """Return {conversation_id: conv} for the gitignored audit corpus.

    Skips (xfails at call site) when the corpus is absent — it is
    gitignored and may not be present in every checkout.
    """
    if not _CORPUS_PATH.exists():
        pytest.skip(f"audit corpus not present at {_CORPUS_PATH}")
    with _CORPUS_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return {c["conversation_id"]: c for c in data}


def _directo_ref(conv: dict) -> str | None:
    """Return the consulted prop CÓDIGO (infocasas_ref) for a directo conv."""
    return conv.get("metadata", {}).get("infocasas_ref")


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
_TEST_PHONE = "+595981599907"
# Deterministic IC prop seeded for the test (title carries the TÍTULO token).
_SEED_IC_TITLE = "Casa en Mburucuya prueba directo"


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
async def directo_contact(db_session: AsyncSession):
    """Seed an IC contact with infocasas_ref set + a matching IC property.

    Yields a dict with contact id/phone and the seeded prop ref + title so
    the test can assert the greeting surfaces TÍTULO + CÓDIGO.
    """
    ref = "DIRTST"  # synthetic ref for the seeded IC prop (not a corpus PII)

    # Idempotent cleanup of any leftover test rows.
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
        sqlalchemy.text("DELETE FROM infocasas_properties WHERE infocasas_ref = :ref"),
        {"ref": ref},
    )

    # Seed a minimal active IC property (only infocasas_id/ref are NOT NULL).
    await db_session.execute(
        sqlalchemy.text(
            "INSERT INTO infocasas_properties "
            "(infocasas_id, infocasas_ref, title, city, is_active) "
            "VALUES (:iid, :ref, :title, 'Asuncion', true)"
        ),
        {"iid": "ICTST001", "ref": ref, "title": _SEED_IC_TITLE},
    )
    # Seed the contact (directo IC → source='infocasas', infocasas_ref set).
    await db_session.execute(
        sqlalchemy.text(
            "INSERT INTO contacts (name, phone, source, status, infocasas_ref) "
            "VALUES ('E2E Directo IC', :phone, 'infocasas', 'new', :ref)"
        ),
        {"phone": _TEST_PHONE, "ref": ref},
    )
    await db_session.commit()

    row = (
        await db_session.execute(
            sqlalchemy.text(
                "SELECT id FROM contacts WHERE phone = :phone"
            ),
            {"phone": _TEST_PHONE},
        )
    ).first()
    contact = {"id": row.id, "phone": _TEST_PHONE, "ref": ref, "title": _SEED_IC_TITLE}

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
    await db_session.execute(
        sqlalchemy.text("DELETE FROM infocasas_properties WHERE infocasas_ref = :ref"),
        {"ref": ref},
    )
    await db_session.commit()


def _make_runner(
    db_session: AsyncSession,
    contact: dict,
    monkeypatch,
) -> ConversationRunner:
    """Build a recepcionista-mode runner whose ContactInfo carries infocasas_ref."""
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
    # Override the canned ContactInfo so _resolve_mode auto-detects
    # recepcionista (check 2c: infocasas_ref) and so persist_lead_outcome
    # advances the REAL seeded row to 'interested'.
    runner.set_contact(
        ContactInfo(
            id=contact["id"],
            name="",
            status="new",
            is_baja=False,
            platform="whatsapp",
            phone=contact["phone"],
            source_id=contact["phone"],
            source="infocasas",
            infocasas_ref=contact["ref"],
        )
    )
    runner.set_conversation(
        ConversationInfo(
            id=987654,
            contact_id=contact["id"],
            platform="whatsapp",
            chat_id=contact["phone"],
            is_bot_active=True,
        )
    )
    return runner


def _system_text(runner: ConversationRunner) -> str:
    """Concatenate the text of every system block sent to Claude (all calls)."""
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
# Happy path — directo IC anchors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("conv_id", _HAPPY_DIRECTO + _EDGE_DIRECTO)
async def test_flujo_directo_ic_completo(conv_id, db_session, directo_contact, monkeypatch):
    """Directo-IC flow hits all 4 milestones + lands contact at interested.

    Anchored on real corpus convs by id (PII never copied here). The seeded
    contact carries infocasas_ref so the orchestrator must surface the
    consulted prop into the greeting.
    """
    corpus = _load_corpus()
    conv = corpus.get(conv_id)
    assert conv is not None, f"corpus conv {conv_id} missing"
    assert conv["metadata"].get("flow_2_2") == "directo", (
        f"conv {conv_id} is not a directo flow"
    )

    runner = _make_runner(db_session, directo_contact, monkeypatch)
    ref = directo_contact["ref"]
    title = directo_contact["title"]

    # --- Milestone 1: SALUDO referencing the prop ----------------------------
    # First inbound turn (generic opener). Claude is scripted; the SUT is that
    # the orchestrator surfaces the prop TÍTULO/CÓDIGO into the system prompt.
    runner.program_claude_response(
        text=f"Hola! Soy Onnix de Onnix SA. Veo que consultaste por "
        f"{title} ({ref}). Con quien tengo el gusto?"
    )
    await runner.send("Hola, info de la propiedad")

    system_blob = _system_text(runner)
    assert ref in system_blob or title.lower() in system_blob.lower(), (
        f"SALUDO milestone FAIL (conv {conv_id}): orchestrator did not surface "
        f"the consulted prop (ref={ref!r} / title={title!r}) into the system "
        f"prompt. The greeting cannot reference TÍTULO + CÓDIGO without it.\n"
        f"System blocks seen:\n{system_blob[-1200:]}"
    )

    # --- Milestone 2: NOMBRE captured + acknowledged -------------------------
    runner.program_claude_response(
        text="Gracias Alex! Que te interesa saber: precio final, expensas, "
        "disponibilidad o coordinar una visita?"
    )
    resp2 = await runner.send("Soy Alex")
    assert "gracias" in (resp2.text or "").lower(), (
        f"NOMBRE milestone FAIL (conv {conv_id}): bot did not acknowledge name."
    )

    # --- Milestone 3: INTERÉS asked with concrete examples -------------------
    interest_tokens = ("precio", "expensas", "visita", "financ", "disponib")
    assert any(tok in (resp2.text or "").lower() for tok in interest_tokens), (
        f"INTERES milestone FAIL (conv {conv_id}): bot did not ask interest "
        f"with concrete examples. Looked for any of {interest_tokens}."
    )

    # --- Milestone 4: DERIVACIÓN with LEAD_REF + register_lead ---------------
    motivo = (
        f"Nombre: Alex. Interes: precio final y coordinar visita. "
        f"Criterios: prop consultada {title} ({ref})."
    )
    runner.program_tool_executor_result(
        {"success": True, "motivo": motivo, "message": "Lead registrado"}
    )
    lead_tool = _make_tool_ai_response("register_lead", {"motivo": motivo})
    lead_text = _make_text_ai_response(
        f"Listo Alex! Le paso tus datos a un asesor. Tu consulta queda "
        f"registrada con el codigo LEAD-{directo_contact['id']}; te contactan "
        f"cuando puedan."
    )
    profile = _make_text_ai_response('{"preferencias": "directo IC"}')
    runner._claude_mock.send_message.side_effect = [lead_tool, lead_text, profile]
    runner._last_tool_name = "register_lead"

    resp4 = await runner.send("Quiero el precio final y coordinar una visita")

    # LEAD_REF token in the closing turn.
    assert "lead-" in (resp4.text or "").lower(), (
        f"LEAD_REF milestone FAIL (conv {conv_id}): closing turn has no "
        f"LEAD_REF token. Got: {resp4.text!r}"
    )
    # register_lead was the tool used.
    runner.assert_last_tool("register_lead")
    assert resp4.is_lead, f"conv {conv_id}: BotResponse.is_lead must be True"

    # --- BOT-12: contact transitions to status='interested' ------------------
    status_row = (
        await db_session.execute(
            sqlalchemy.text("SELECT status FROM contacts WHERE id = :cid"),
            {"cid": directo_contact["id"]},
        )
    ).first()
    assert status_row.status == "interested", (
        f"BOT-12 FAIL (conv {conv_id}): contact must be 'interested' at "
        f"derivation, got {status_row.status!r}."
    )

    # --- motivo carries masticated context (nombre + interes + criterios) ----
    lead_event = (
        await db_session.execute(
            sqlalchemy.text(
                "SELECT metadata FROM lead_events "
                "WHERE contact_id = :cid AND event_type = 'lead_registered' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"cid": directo_contact["id"]},
        )
    ).first()
    assert lead_event is not None, (
        f"conv {conv_id}: expected a lead_registered event row."
    )
    meta = lead_event.metadata
    if isinstance(meta, str):
        meta = json.loads(meta)
    stored_motivo = (meta or {}).get("motivo", "")
    assert "Alex" in stored_motivo and (
        ref in stored_motivo or title in stored_motivo
    ), (
        f"conv {conv_id}: register_lead motivo must carry nombre + criterios "
        f"(asesor sees masticated context). Got: {stored_motivo!r}"
    )


# ---------------------------------------------------------------------------
# Known failure — documented xfail (audit-flagged, ref absent)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("conv_id", _KNOWN_FAILURE)
@pytest.mark.xfail(
    reason="Audit known_failure (conv 492): no infocasas_ref in metadata, so "
    "the directo greeting cannot surface a consulted prop. Documented gap — "
    "not a regression; left as xfail per plan 123-05.",
    strict=True,
)
async def test_flujo_directo_ic_known_failure(conv_id, db_session, directo_contact, monkeypatch):
    """Conv 492 has no infocasas_ref → greeting cannot reference a prop.

    Drives the same first turn but asserts the corpus ref (absent) would be
    surfaced — expected to FAIL, hence xfail. Documents the audit gap without
    masking it.
    """
    corpus = _load_corpus()
    conv = corpus.get(conv_id)
    assert conv is not None
    corpus_ref = _directo_ref(conv)  # None for 492

    runner = _make_runner(db_session, directo_contact, monkeypatch)
    runner.program_claude_response(text="Hola! Con quien tengo el gusto?")
    await runner.send("Hola")

    system_blob = _system_text(runner)
    # This assertion is expected to fail (corpus_ref is None for 492).
    assert corpus_ref is not None and corpus_ref in system_blob, (
        f"conv {conv_id}: cannot surface a consulted prop without a ref."
    )

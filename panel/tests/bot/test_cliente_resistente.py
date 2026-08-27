"""E2E — M6.3 BOT-09: flujo "cliente resistente" (recepcionista mode).

Proves the *defensive* derivation path: when a lead either says
"pasame al asesor ya" OR refuses to answer the recepcionista's cordial
questions after 2 attempts, the bot derives ANYWAY with whatever partial
info it has (``register_lead``) and the SYSTEM flags the capture as partial
for the asesor — ``lead_events.metadata.partial_capture == true`` (BOT-09).
The contact still transitions to ``status='interested'``.

COVERAGE HONESTY (read this — load-bearing)
-------------------------------------------
The audit corpus has only **2 real resistente exemplars** — conversations
**193** and **226** (referenced by id ONLY; both are audit-flagged
``selected_for=='known_failure'`` under the OLD bot — it never did the
defensive derivation, the exact M6.3 gap). 2 real exemplars cannot cover
BOTH resistente sub-paths, so this suite ALSO uses **1 clearly-labeled
SYNTHETIC fixture** (``fixtures/resistente_synthetic.json``,
``"synthetic": true``) to cover the second sub-path:

  - Real anchors 193, 226  → "no responde 2 turnos" sub-path (replayed by id;
    SKIP when the gitignored corpus is absent from this checkout).
  - Synthetic fixture       → "pasame al asesor ya" sub-path (committed,
    PII-free, FABRICATED — never passed off as real corpus).

The synthetic is documented as synthetic everywhere it is used; it does NOT
inflate real-corpus coverage.

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

# The ONLY 2 real resistente exemplars in the audit corpus (by id).
# Both are audit-flagged known_failure (the OLD bot never derived defensively).
_REAL_RESISTENTE = ["193", "226"]

# Labeled SYNTHETIC fixture — covers the "pasame al asesor ya" sub-path the
# 2 real exemplars don't. FABRICATED, PII-free; documented as synthetic.
_SYNTHETIC_PATH = Path(__file__).resolve().parent / "fixtures" / "resistente_synthetic.json"


def _load_corpus() -> dict[str, dict]:
    """Return {conversation_id: conv} for the gitignored audit corpus.

    SKIPS when the corpus is absent — it is gitignored and may not be present
    in every checkout. The synthetic fixture path always runs regardless.
    """
    if not _CORPUS_PATH.exists():
        pytest.skip(f"audit corpus not present at {_CORPUS_PATH}")
    with _CORPUS_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return {c["conversation_id"]: c for c in data}


def _load_synthetic() -> dict:
    """Return the labeled synthetic resistente turn-script (committed)."""
    with _SYNTHETIC_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


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
_TEST_PHONE = "+595981599908"


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
async def resistente_contact(db_session: AsyncSession):
    """Seed a plain WhatsApp lead with NO origin data and NO name.

    Resistente = the client refuses to give details, so the contact has no
    name at derivation. The bot derives anyway (defensive path) and the
    system flags partial_capture.
    """
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
            "VALUES (NULL, :phone, 'whatsapp', 'new')"
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


def _make_runner(
    db_session: AsyncSession,
    contact: dict,
    monkeypatch,
) -> ConversationRunner:
    """Recepcionista-mode runner for a resistente lead (no origin, no name)."""
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
    # Force recepcionista via _resolve_mode check 1 (per-chat override).
    runner.set_search_context(ConversationState(mode="recepcionista"))
    runner.set_conversation(
        ConversationInfo(
            id=765431,
            contact_id=contact["id"],
            platform="whatsapp",
            chat_id=contact["phone"],
            is_bot_active=True,
        )
    )
    return runner


async def _derive_and_assert(
    runner: ConversationRunner,
    db_session: AsyncSession,
    contact: dict,
    *,
    label: str,
) -> None:
    """Drive the runner to the defensive derivation and assert BOT-09.

    Scripts the resistente derivation: the client gives no usable details, so
    the bot fires register_lead with a partial motivo. Asserts:
      - register_lead was the tool used (the bot DERIVED, did not keep looping),
      - lead_events.metadata.partial_capture == True,
      - contact.status == 'interested'.
    """
    # Partial motivo — the bot derives with what it has (no nombre/criterios).
    motivo = "Cliente resistente: no dio nombre ni criterios. Captura parcial."
    runner.program_tool_executor_result(
        {"success": True, "motivo": motivo, "message": "Lead registrado"}
    )
    lead_tool = _make_tool_ai_response("register_lead", {"motivo": motivo})
    lead_text = _make_text_ai_response(
        f"Listo! Le paso tus datos a un asesor. Tu consulta queda registrada "
        f"con el codigo LEAD-{contact['id']}; te contactan cuando puedan."
    )
    profile = _make_text_ai_response('{"preferencias": "resistente"}')
    runner._claude_mock.send_message.side_effect = [lead_tool, lead_text, profile]
    runner._last_tool_name = "register_lead"

    resp = await runner.send("pasame al asesor ya")

    # The bot DERIVED (did not loop/insist): register_lead fired.
    runner.assert_last_tool("register_lead")
    assert resp.is_lead, f"{label}: BotResponse.is_lead must be True"

    # BOT-12: contact transitions to status='interested'.
    status_row = (
        await db_session.execute(
            sqlalchemy.text("SELECT status FROM contacts WHERE id = :cid"),
            {"cid": contact["id"]},
        )
    ).first()
    assert status_row.status == "interested", (
        f"{label}: resistente derivation must land contact at 'interested', "
        f"got {status_row.status!r}."
    )

    # BOT-09: lead_events.metadata.partial_capture == True.
    lead_event = (
        await db_session.execute(
            sqlalchemy.text(
                "SELECT metadata FROM lead_events "
                "WHERE contact_id = :cid AND event_type = 'lead_registered' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"cid": contact["id"]},
        )
    ).first()
    assert lead_event is not None, f"{label}: expected a lead_registered event row."
    meta = lead_event.metadata
    if isinstance(meta, str):
        meta = json.loads(meta)
    assert (meta or {}).get("partial_capture") is True, (
        f"{label}: BOT-09 — resistente derivation must set "
        f"lead_events.metadata.partial_capture=true. Got metadata: {meta!r}"
    )


# ---------------------------------------------------------------------------
# Real anchors — 193, 226 (the ONLY 2 real resistente exemplars, by id)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("conv_id", _REAL_RESISTENTE)
async def test_cliente_resistente_2_intentos_real(
    conv_id, db_session, resistente_contact, monkeypatch
):
    """REAL corpus resistente exemplars (193, 226, by id) — defensive derivation.

    These are the ONLY 2 real resistente convs (both audit known_failure under
    the OLD bot). Replayed in recepcionista mode: after the cordial questions
    go unanswered, the bot derives with partial info and the system flags
    partial_capture. SKIPS when the gitignored corpus is absent.
    """
    corpus = _load_corpus()
    conv = corpus.get(conv_id)
    assert conv is not None, f"corpus conv {conv_id} missing"
    assert conv["metadata"].get("flow_2_2") == "resistente", (
        f"conv {conv_id} is not a resistente flow"
    )

    runner = _make_runner(db_session, resistente_contact, monkeypatch)
    await _derive_and_assert(
        runner, db_session, resistente_contact, label=f"real conv {conv_id}",
    )


# ---------------------------------------------------------------------------
# Synthetic fixture — covers the "pasame al asesor ya" sub-path (LABELED)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cliente_resistente_synthetic(
    db_session, resistente_contact, monkeypatch
):
    """SYNTHETIC (labeled, fabricated) resistente exemplar — "pasame ya" sub-path.

    NOT real corpus. The fixture is explicitly ``"synthetic": true`` and exists
    only because the 2 real exemplars (193, 226) cannot cover both sub-paths.
    Asserts the same BOT-09 contract: defensive derivation +
    partial_capture=true + status='interested'.
    """
    synth = _load_synthetic()
    # Guard: the fixture MUST be labeled synthetic (honest coverage).
    assert synth.get("synthetic") is True, (
        "resistente_synthetic.json must be labeled synthetic (synthetic: true)"
    )
    assert "fabricated" in (synth.get("note") or "").lower(), (
        "synthetic fixture must carry a 'fabricated' note"
    )

    runner = _make_runner(db_session, resistente_contact, monkeypatch)
    await _derive_and_assert(
        runner, db_session, resistente_contact, label="synthetic",
    )

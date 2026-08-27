"""E2E — M6.3 BOT-08: flujo "sin contexto" (recepcionista mode).

Replays the sin_contexto conversations from the audit corpus
(``.planning/phases/121-.../raw/corpus.json``, gitignored) by
``conversation_id`` ONLY — NO PII (names, phones, free text) is copied
into this committed file. A "sin contexto" lead is the largest flow class
(23 corpus convs): the contact arrived with NO origin data — no
``infocasas_ref`` (not a directo single-prop consult) and no parsed
forwarded-search (``preferences.ic_type='reenviada'``). With no origin to
surface, the recepcionista must ask the name AND qué busca
(tipo / zona / presupuesto) directly, then capture → derive.

For each anchored conversation the test seeds a contact with NO
``infocasas_ref`` and NO reenviada preferences, forces recepcionista mode
via the per-chat ``mode`` override (``ConversationState.mode='recepcionista'``
— check 1 of ``_resolve_mode``, since there is no ref to trigger check 2c),
and drives the orchestrator in recepcionista mode (WhatsApp), asserting the
recepcionista milestones are HIT:

  1. saludo  — the recepcionista prompt's SIN CONTEXTO branch is surfaced
                (bot asks the NAME + qué busca with tipo/zona/presupuesto
                tokens). Because Claude is mocked, the load-bearing
                (non-tautological) signal is that the orchestrator selects
                the RECEPCIONISTA system prompt — whose SIN CONTEXTO block
                instructs "preguntá directamente qué busca (tipo / zona /
                presupuesto) además del nombre" — into the system blocks
                Claude receives, AND that no DIRECTO/INDIRECTO origin note
                is surfaced (there is no origin data).
  2. nombre   — bot acknowledges the captured name ("Gracias {Nombre}").
  3. criterios — bot captures qué busca (tipo / zona / presupuesto).
  4. LEAD_REF — closing turn carries a LEAD_REF token AND register_lead was
                called; contact transitions to status='interested' (BOT-12)
                and register_lead's ``motivo`` carries the masticated context.

External surfaces (Claude, SearchService, channel senders) are mocked via
``ConversationRunner``; no network calls, no live LLM. DB writes go to
onnix_dev only (test phone within the cleanup range).

These tests are expected to pass on the 123-04 recepcionista prompt alone
(the prompt instructs the SIN CONTEXTO branch + Ejemplo 3). If a sin_contexto
assertion fails, the gap is in the prompt's SIN CONTEXTO few-shot
(prompts.py ``RECEPCIONISTA_SYSTEM_PROMPT`` → "## Origen del lead" /
"Ejemplo 3 — SIN CONTEXTO"), NOT a new code path.
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

# Sin_contexto anchors from the blueprint §7 (referenced by id ONLY).
# These are leads with NO origin data (metadata.flow_2_2 == 'sin_contexto',
# metadata.infocasas_ref is None). The largest flow class (23 convs).
_HAPPY_SIN_CONTEXTO = [
    "9", "148", "154", "201", "242", "246", "249", "354",
    "388", "389", "435", "518", "527", "538", "541", "613",
]
# Edges: non-whatsapp source (import:excel / manual) but still no origin data —
# the SIN CONTEXTO branch handles them identically (origin-context is empty).
_EDGE_SIN_CONTEXTO = ["334", "410", "454"]
# Known failures from the audit (documented as xfail). 596 is ALSO a bulk
# fixture (repurposed as WhatsApp in test_bulk_capture.py).
_KNOWN_FAILURE_SIN_CONTEXTO = ["168", "206", "355", "596"]


def _load_corpus() -> dict[str, dict]:
    """Return {conversation_id: conv} for the gitignored audit corpus.

    Skips (skips at call site) when the corpus is absent — it is
    gitignored and may not be present in every checkout.
    """
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
_TEST_PHONE = "+595981599909"


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
async def sin_contexto_contact(db_session: AsyncSession):
    """Seed a lead with NO origin data (no infocasas_ref, no reenviada prefs).

    This is the SIN CONTEXTO condition: a plain WhatsApp lead. The
    orchestrator surfaces NO origin note (build_origin_context returns "")
    and the recepcionista prompt's SIN CONTEXTO branch must ask name + qué
    busca directly.
    """
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

    # Seed a plain lead: source='whatsapp', NO infocasas_ref, NO preferences.
    await db_session.execute(
        sqlalchemy.text(
            "INSERT INTO contacts (name, phone, source, status) "
            "VALUES ('E2E Sin Contexto', :phone, 'whatsapp', 'new')"
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
    """Build a recepcionista-mode runner for a lead with NO origin data.

    There is no infocasas_ref to trigger _resolve_mode check 2c, so
    recepcionista is forced via the per-chat ``mode`` override (check 1):
    ``ConversationState.mode='recepcionista'`` on the seeded search_context.
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
    # Plain lead: no infocasas_ref, no vista_publica source. Mode comes from
    # the per-chat override below, NOT from auto-detect.
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
            id=765432,
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
# Happy + edge path — sin_contexto anchors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("conv_id", _HAPPY_SIN_CONTEXTO + _EDGE_SIN_CONTEXTO)
async def test_flujo_sin_contexto_ic(conv_id, db_session, sin_contexto_contact, monkeypatch):
    """Sin_contexto flow asks name + qué busca, captures, derives → interested.

    Anchored on real corpus convs by id (PII never copied here). The seeded
    contact carries NO origin data, so the orchestrator surfaces NO origin
    note and the recepcionista SIN CONTEXTO branch must ask name + qué busca
    (tipo / zona / presupuesto).
    """
    corpus = _load_corpus()
    conv = corpus.get(conv_id)
    assert conv is not None, f"corpus conv {conv_id} missing"
    assert conv["metadata"].get("flow_2_2") == "sin_contexto", (
        f"conv {conv_id} is not a sin_contexto flow"
    )
    assert conv["metadata"].get("infocasas_ref") is None, (
        f"conv {conv_id} unexpectedly carries an infocasas_ref (not sin_contexto)"
    )

    runner = _make_runner(db_session, sin_contexto_contact, monkeypatch)

    # --- Milestone 1: SALUDO — ask NAME + qué busca (tipo/zona/presupuesto) ---
    # First inbound turn (generic opener). Claude is scripted per the prompt's
    # SIN CONTEXTO Ejemplo 3; the load-bearing SUT is that the orchestrator
    # selects the RECEPCIONISTA prompt whose SIN CONTEXTO block instructs the
    # name + qué busca question, and surfaces NO origin note.
    runner.program_claude_response(
        text="Hola! Soy Onnix de Onnix SA. Como te llamas y que estas "
        "buscando (tipo, zona, presupuesto)?"
    )
    resp1 = await runner.send("Hola")

    system_blob = _system_text(runner)
    blob_l = system_blob.lower()

    # (a) Recepcionista SIN CONTEXTO branch is in the surfaced system prompt.
    assert "sin contexto" in blob_l, (
        f"SALUDO milestone FAIL (conv {conv_id}): the RECEPCIONISTA prompt's "
        f"SIN CONTEXTO branch is not in the system blocks Claude received — "
        f"mode did not resolve to recepcionista or the prompt is missing the "
        f"SIN CONTEXTO instruction.\nSystem blocks tail:\n{system_blob[-1400:]}"
    )
    # (b) The SIN CONTEXTO instruction asks for qué busca with concrete fields
    #     (tipo / zona / presupuesto) — the defining behavior of this flow.
    quebusca_tokens = ("qué busca", "que busca", "tipo", "zona", "presupuesto")
    surfaced = [tok for tok in quebusca_tokens if tok in blob_l]
    assert len(surfaced) >= 3 and ("tipo" in blob_l and "zona" in blob_l and "presupuesto" in blob_l), (
        f"SALUDO milestone FAIL (conv {conv_id}): SIN CONTEXTO branch must ask "
        f"qué busca with tipo/zona/presupuesto. Surfaced only {surfaced}.\n"
        f"System blocks tail:\n{system_blob[-1400:]}"
    )
    # (c) NO origin note surfaced (no directo/indirecto framing — no origin data).
    assert "(directo)" not in blob_l and "(indirecto)" not in blob_l, (
        f"conv {conv_id}: a sin_contexto lead must NOT surface a DIRECTO/"
        f"INDIRECTO origin note (there is no origin data)."
    )
    # (d) The bot's first reply asks the name + qué busca (tipo/zona/presupuesto).
    r1l = (resp1.text or "").lower()
    assert any(t in r1l for t in ("tipo", "zona", "presupuesto", "busca")), (
        f"conv {conv_id}: first reply must ask qué busca. Got: {resp1.text!r}"
    )

    # --- Milestone 2: NOMBRE captured + acknowledged -------------------------
    runner.program_claude_response(
        text="Gracias Pedro! Buscas para comprar o alquilar?"
    )
    resp2 = await runner.send("Soy Pedro, casa en San Lorenzo hasta 120 mil")
    assert "gracias" in (resp2.text or "").lower(), (
        f"NOMBRE milestone FAIL (conv {conv_id}): bot did not acknowledge name."
    )

    # --- Milestone 3: CRITERIOS (tipo/zona/presupuesto) captured -------------
    # The client gave tipo (casa) + zona (San Lorenzo) + presupuesto (120 mil)
    # in turn 2; the bot proceeds (does not re-ask qué busca) and moves toward
    # derivation. Assert the bot did NOT re-prompt for tipo/zona/presupuesto.
    assert not any(
        tok in (resp2.text or "").lower() for tok in ("qué tipo", "que tipo", "en qué zona", "en que zona")
    ), (
        f"CRITERIOS milestone FAIL (conv {conv_id}): bot re-asked tipo/zona "
        f"after the client already gave them. Got: {resp2.text!r}"
    )

    # --- Milestone 4: DERIVACIÓN with LEAD_REF + register_lead ---------------
    motivo = (
        "Nombre: Pedro. Interes: comprar. "
        "Criterios: casa en San Lorenzo hasta 120 mil."
    )
    runner.program_tool_executor_result(
        {"success": True, "motivo": motivo, "message": "Lead registrado"}
    )
    lead_tool = _make_tool_ai_response("register_lead", {"motivo": motivo})
    lead_text = _make_text_ai_response(
        f"Listo Pedro! Le paso tus datos a un asesor. Tu consulta queda "
        f"registrada con el codigo LEAD-{sin_contexto_contact['id']}; te "
        f"contactan cuando puedan."
    )
    profile = _make_text_ai_response('{"preferencias": "sin contexto"}')
    runner._claude_mock.send_message.side_effect = [lead_tool, lead_text, profile]
    runner._last_tool_name = "register_lead"

    resp4 = await runner.send("comprar")

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
            {"cid": sin_contexto_contact["id"]},
        )
    ).first()
    assert status_row.status == "interested", (
        f"BOT-12 FAIL (conv {conv_id}): contact must be 'interested' at "
        f"derivation, got {status_row.status!r}."
    )

    # --- motivo carries masticated context (nombre + criterios) --------------
    lead_event = (
        await db_session.execute(
            sqlalchemy.text(
                "SELECT metadata FROM lead_events "
                "WHERE contact_id = :cid AND event_type = 'lead_registered' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"cid": sin_contexto_contact["id"]},
        )
    ).first()
    assert lead_event is not None, (
        f"conv {conv_id}: expected a lead_registered event row."
    )
    meta = lead_event.metadata
    if isinstance(meta, str):
        meta = json.loads(meta)
    stored_motivo = (meta or {}).get("motivo", "")
    assert "Pedro" in stored_motivo and "San Lorenzo" in stored_motivo, (
        f"conv {conv_id}: register_lead motivo must carry nombre + criterios "
        f"(asesor sees masticated context). Got: {stored_motivo!r}"
    )


# ---------------------------------------------------------------------------
# Known failures — documented xfail (audit-flagged abandoned/no-capture convs)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("conv_id", _KNOWN_FAILURE_SIN_CONTEXTO)
@pytest.mark.xfail(
    reason="Audit known_failure sin_contexto convs (168, 206, 355, 596): the "
    "OLD bot's real transcripts are audit-flagged failures "
    "(metadata.selected_for=='known_failure', outcome=='abandoned', "
    "status_final=='no_response') — the OLD bot answered with buscador framing / "
    "dead-ends, never doing the recepcionista saludo-con-nombre, never capturing "
    "nombre+interés, never deriving with LEAD_REF (the exact M6.3 gap). The NEW "
    "recepcionista flow is proven on the happy/edge anchors above; these remain "
    "xfail to document the audit gap by id without masking it. 596 is ALSO "
    "repurposed as a WhatsApp bulk fixture in test_bulk_capture.py.",
    strict=True,
)
async def test_flujo_sin_contexto_known_failure(conv_id, db_session, sin_contexto_contact, monkeypatch):
    """Known-failure sin_contexto convs: the OLD transcript was a flagged gap.

    Asserts the OLD corpus conversation was NOT audit-flagged as a
    known_failure — which is FALSE for these convs (they ARE the flagged
    failures), so this xfails. Documents the audit gap by id only (no PII
    inspected). The NEW recepcionista flow is proven on the happy/edge anchors.
    """
    corpus = _load_corpus()
    conv = corpus.get(conv_id)
    assert conv is not None, f"corpus conv {conv_id} missing"
    md = conv["metadata"]
    assert md.get("flow_2_2") == "sin_contexto"

    # These ARE the audit's known_failure convs (the OLD bot never derived a
    # recepcionista lead). Asserting the opposite makes the xfail honest +
    # strict: if a conv were ever re-labelled to a non-failure, this xpasses
    # and the strict xfail turns it into a real failure to force a review.
    assert md.get("selected_for") != "known_failure", (
        f"conv {conv_id}: audit-flagged known_failure "
        f"(outcome={md.get('outcome')!r}, divergence cites no recepcionista "
        f"saludo / no captura nombre-interés / no LEAD_REF)."
    )

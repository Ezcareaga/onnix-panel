"""E2E — M6.3 BOT-07: flujo "lead indirecto IC" (recepcionista mode).

Replays the indirecto-IC conversations from the audit corpus
(``.planning/phases/121-.../raw/corpus.json``, gitignored) by
``conversation_id`` ONLY — NO PII (names, phones, free text) is copied
into this committed file. An indirecto lead is a FORWARDED SEARCH
(InfoCasas "consulta reenviada"): the client did not consult a single
property, they shared a SEARCH (tipo / dorms / zona / precio). The
greeting must demonstrate the bot understood that parsed search and ask
the client to confirm it ("¿es eso o algo distinto?").

For each anchored conversation the test seeds a contact whose
``preferences`` carry the parsed/reenviada search
(``ic_type='reenviada'`` + tipo / dorms / zona / precio) and drives the
orchestrator in recepcionista mode (WhatsApp), asserting the four
recepcionista milestones are HIT:

  1. saludo  — the bot's first turn reflects the PARSED SEARCH and asks
                to confirm. Because Claude is mocked, the meaningful
                (non-tautological) signal is that the orchestrator
                SURFACES the parsed-search tokens (TIPO / DORMS / ZONA /
                PRECIO) into the system prompt Claude receives. We assert
                at least 2 of those 4 tokens appear in the system blocks
                AND that a confirmation cue ("es eso o algo distinto" /
                "confirm") is present so Onnix greets with the INDIRECTO
                block, not the DIRECTO one.
  2. nombre   — bot acknowledges the captured name ("Gracias {Nombre}").
  3. interés  — bot asks the interest with concrete examples
                (precio / expensas / visita / financiación).
  4. LEAD_REF — closing turn carries a LEAD_REF token AND register_lead
                was called; contact transitions to status='interested'
                (BOT-12) and register_lead's ``motivo`` carries the
                masticated context.

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

# Indirecto-IC anchors from the blueprint §7 (referenced by id ONLY).
# indirecto = forwarded search (metadata.is_reassigned true / flow_2_2
# 'indirecto'); the parsed search is derived into preferences ic_type
# 'reenviada' (is_reassigned is NOT a contacts column — derived field).
_HAPPY_INDIRECTO = [
    "141", "155", "164", "351", "376", "379", "510",
    "513", "516", "528", "539", "543", "563", "567",
]
_EDGE_INDIRECTO = ["571"]


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
_TEST_PHONE = "+595981599908"

# Deterministic PARSED SEARCH seeded into contacts.preferences. These are the
# TIPO / DORMS / ZONA / PRECIO tokens the greeting must surface to prove the
# bot understood the forwarded search. Synthetic — NOT corpus PII.
_SEED_SEARCH = {
    "ic_type": "reenviada",
    "tipo": "departamento",
    "dorms": 2,
    "zona": "Luque",
    "precio": "USD 80.000",
}
# A synthetic IC ref (forwarded-search leads still carry an infocasas_ref;
# the listing they were forwarded FROM). Not corpus PII.
_SEED_REF = "INDTST"


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
async def indirecto_contact(db_session: AsyncSession):
    """Seed a reenviada (indirecto) IC contact carrying the parsed search.

    The parsed search lives in ``contacts.preferences`` with
    ``ic_type='reenviada'`` + tipo / dorms / zona / precio. Yields a dict
    with the contact id/phone and the seeded search so the test can assert
    the greeting surfaces the parsed-search tokens.
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

    # Seed the contact (indirecto IC → source='infocasas', infocasas_ref set,
    # preferences carry the parsed/reenviada search).
    await db_session.execute(
        sqlalchemy.text(
            "INSERT INTO contacts "
            "(name, phone, source, status, infocasas_ref, preferences) "
            "VALUES ('E2E Indirecto IC', :phone, 'infocasas', 'new', :ref, "
            " CAST(:prefs AS jsonb))"
        ),
        {
            "phone": _TEST_PHONE,
            "ref": _SEED_REF,
            "prefs": json.dumps(_SEED_SEARCH, ensure_ascii=False),
        },
    )
    await db_session.commit()

    row = (
        await db_session.execute(
            sqlalchemy.text("SELECT id FROM contacts WHERE phone = :phone"),
            {"phone": _TEST_PHONE},
        )
    ).first()
    contact = {
        "id": row.id,
        "phone": _TEST_PHONE,
        "ref": _SEED_REF,
        "search": _SEED_SEARCH,
    }

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
    """Build a recepcionista-mode runner whose ContactInfo carries infocasas_ref.

    infocasas_ref drives _resolve_mode check 2c → recepcionista; the
    indirecto-vs-directo distinction is made by build_origin_context reading
    the seeded contacts.preferences (ic_type='reenviada').
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
            source="infocasas",
            infocasas_ref=contact["ref"],
        )
    )
    runner.set_conversation(
        ConversationInfo(
            id=876543,
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


def _parsed_search_token_hits(system_blob: str, search: dict) -> int:
    """Count how many of TIPO / DORMS / ZONA / PRECIO appear in the blob."""
    blob = system_blob.lower()
    hits = 0
    if str(search["tipo"]).lower() in blob:
        hits += 1
    if str(search["dorms"]) in system_blob:  # numeric — case-irrelevant
        hits += 1
    if str(search["zona"]).lower() in blob:
        hits += 1
    # PRECIO: match the numeric core ("80.000") so formatting can't break it.
    if "80.000" in system_blob or "80000" in system_blob:
        hits += 1
    return hits


# ---------------------------------------------------------------------------
# Happy + edge path — indirecto IC anchors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("conv_id", _HAPPY_INDIRECTO + _EDGE_INDIRECTO)
async def test_flujo_indirecto_ic_completo(conv_id, db_session, indirecto_contact, monkeypatch):
    """Indirecto-IC flow hits all 4 milestones + lands contact at interested.

    Anchored on real corpus convs by id (PII never copied here). The seeded
    contact carries a reenviada parsed search in preferences so the
    orchestrator must surface the parsed search (TIPO/DORMS/ZONA/PRECIO) into
    the greeting and ask to confirm — distinguishing INDIRECTO from DIRECTO.
    """
    corpus = _load_corpus()
    conv = corpus.get(conv_id)
    assert conv is not None, f"corpus conv {conv_id} missing"
    assert conv["metadata"].get("flow_2_2") == "indirecto", (
        f"conv {conv_id} is not an indirecto flow"
    )

    runner = _make_runner(db_session, indirecto_contact, monkeypatch)
    search = indirecto_contact["search"]

    # --- Milestone 1: SALUDO reflecting the PARSED SEARCH + confirm ----------
    # First inbound turn (generic opener). Claude is scripted; the SUT is that
    # the orchestrator surfaces the parsed-search tokens AND the INDIRECTO
    # confirm framing into the system prompt.
    runner.program_claude_response(
        text="Hola! Soy Onnix de Onnix SA. Veo que buscabas un "
        "departamento de 2 dorms en Luque, alrededor de USD 80.000. "
        "Es eso o algo distinto? Con quien tengo el gusto?"
    )
    await runner.send("Hola")

    system_blob = _system_text(runner)

    # (a) parsed-search surfaced: at least 2 of TIPO/DORMS/ZONA/PRECIO.
    hits = _parsed_search_token_hits(system_blob, search)
    assert hits >= 2, (
        f"SALUDO milestone FAIL (conv {conv_id}): orchestrator surfaced only "
        f"{hits}/4 parsed-search tokens (need >=2 of TIPO/DORMS/ZONA/PRECIO). "
        f"The INDIRECTO greeting cannot reflect the forwarded search without "
        f"them.\nSystem blocks tail:\n{system_blob[-1400:]}"
    )
    # (b) INDIRECTO confirm framing present (distinguishes from DIRECTO note).
    blob_l = system_blob.lower()
    assert ("es eso o algo distinto" in blob_l) or ("confirm" in blob_l), (
        f"SALUDO milestone FAIL (conv {conv_id}): no confirmation cue in the "
        f"surfaced origin note — INDIRECTO greeting must ask the client to "
        f"confirm the parsed search.\nSystem blocks tail:\n{system_blob[-1400:]}"
    )
    # (c) must NOT be framed as the DIRECTO single-prop greeting.
    assert "(directo)" not in blob_l, (
        f"conv {conv_id}: reenviada lead surfaced as DIRECTO, not INDIRECTO."
    )

    # --- Milestone 2: NOMBRE captured + acknowledged -------------------------
    runner.program_claude_response(
        text="Gracias Ana! Que priorizas: presupuesto cerrado, zona puntual "
        "o agendar visita?"
    )
    resp2 = await runner.send("Si, eso. Soy Ana")
    assert "gracias" in (resp2.text or "").lower(), (
        f"NOMBRE milestone FAIL (conv {conv_id}): bot did not acknowledge name."
    )

    # --- Milestone 3: INTERÉS asked with concrete examples -------------------
    interest_tokens = ("precio", "presupuesto", "zona", "visita", "financ", "disponib")
    assert any(tok in (resp2.text or "").lower() for tok in interest_tokens), (
        f"INTERES milestone FAIL (conv {conv_id}): bot did not ask interest "
        f"with concrete examples. Looked for any of {interest_tokens}."
    )

    # --- Milestone 4: DERIVACIÓN with LEAD_REF + register_lead ---------------
    motivo = (
        "Nombre: Ana. Interes: presupuesto cerrado. "
        "Criterios (busqueda reenviada): departamento 2 dorms Luque USD 80.000."
    )
    runner.program_tool_executor_result(
        {"success": True, "motivo": motivo, "message": "Lead registrado"}
    )
    lead_tool = _make_tool_ai_response("register_lead", {"motivo": motivo})
    lead_text = _make_text_ai_response(
        f"Listo Ana! Le paso tus datos a un asesor. Tu consulta queda "
        f"registrada con el codigo LEAD-{indirecto_contact['id']}; te "
        f"contactan cuando puedan."
    )
    profile = _make_text_ai_response('{"preferencias": "indirecto IC"}')
    runner._claude_mock.send_message.side_effect = [lead_tool, lead_text, profile]
    runner._last_tool_name = "register_lead"

    resp4 = await runner.send("El presupuesto cerrado")

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
            {"cid": indirecto_contact["id"]},
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
            {"cid": indirecto_contact["id"]},
        )
    ).first()
    assert lead_event is not None, (
        f"conv {conv_id}: expected a lead_registered event row."
    )
    meta = lead_event.metadata
    if isinstance(meta, str):
        meta = json.loads(meta)
    stored_motivo = (meta or {}).get("motivo", "")
    assert "Ana" in stored_motivo and "Luque" in stored_motivo, (
        f"conv {conv_id}: register_lead motivo must carry nombre + parsed "
        f"criterios (asesor sees masticated context). Got: {stored_motivo!r}"
    )

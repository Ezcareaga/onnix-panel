#!/usr/bin/env python3
"""M6.3 Phase 124-02 — in-process real-LLM recepcionista UAT replay runner.

Replays the ORIGINAL inbound customer messages of the 5 hardest corpus
conversations (the documented OLD-bot known-failures 492 / 168 / 206 / 355 /
596) through the NEW recepcionista bot and asserts correct recepcionista
OUTCOMES.

Method (LOCKED by plan 124-02):
- Drives the REAL ``Orchestrator`` with the REAL ``ClaudeClient`` (real Claude,
  reads ANTHROPIC_API_KEY from .env), the REAL ``ConversationManager`` (DB
  writes land in onnix_dev), the REAL ``ToolExecutor``, and a REAL
  ``CircuitBreaker``.
- The channel sender is NEVER reachable: ``Orchestrator.handle_message``
  returns a ``BotResponse`` directly (the sender lives in MessageHandler, one
  layer up, which this runner does NOT use). The outbound the client WOULD see
  is captured from each ``BotResponse.text``. Zero Twilio calls are attempted.
  WA_SEND_ENABLED=false in the staging container is the backstop; this runner
  needs no backstop because no send path is constructed.
- ``SearchService`` is replaced with a record-and-return-empty SPY. In
  recepcionista mode a search call is itself a leak / over-eager-switch signal,
  so the spy records every invocation and returns zero properties.
- Admin notifier + opt-out/error text getters are patched so nothing leaves
  the process.

Runs on the HOST against onnix_dev on 127.0.0.1:5432 (NOT prod). Seeds its
own test contacts under the +595981599xxx cleanup prefix, asserts DB outcomes,
then deletes every seeded row. NEVER touches onnix_prod.

Output: per-conv structured result (machine JSON block + human-readable),
outbound payloads REDACTED (phone/email → lengths/shape). Cleans up at the end.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Path + env wiring BEFORE any app import (mirror the E2E test modules).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PANEL_DIR = _REPO_ROOT / "panel"
if str(_PANEL_DIR) not in sys.path:
    sys.path.insert(0, str(_PANEL_DIR))

# Load .env (ANTHROPIC_API_KEY, POSTGRES_PASSWORD, ...) from the repo root.
_ENV_PATH = _REPO_ROOT / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _k = _k.strip()
        _v = _v.strip().strip('"').strip("'")
        # Do not clobber anything already exported in the shell.
        os.environ.setdefault(_k, _v)

# Force dev DB + silence external services BEFORE any app import.
os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ["POSTGRES_DB"] = "onnix_dev"
os.environ["TELEGRAM_EZ_CHAT_ID"] = ""
os.environ["FOLLOWUP_SENDER_ENABLED"] = "false"
os.environ["INFOCASAS_POLL_ENABLED"] = "false"
os.environ["WA_SEND_ENABLED"] = "false"
# .env carries the CONTAINER geo path (/app/data/geografia). This runner runs
# on the HOST, where the geo JSONs live under the repo. Point the prompt
# builder at the host path so the geography section resolves.
_HOST_GEO = _REPO_ROOT / "data" / "geografia"
if _HOST_GEO.is_dir():
    os.environ["GEO_DATA_PATH"] = str(_HOST_GEO)

import sqlalchemy  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.bot.ai.circuit_breaker import CircuitBreaker  # noqa: E402
from app.bot.ai.claude_client import ClaudeClient  # noqa: E402
from app.bot.config import bot_settings  # noqa: E402
from app.bot.core.conversation import ConversationManager  # noqa: E402
from app.bot.core.orchestrator import Orchestrator  # noqa: E402
from app.bot.core.response_builder import ResponseBuilder  # noqa: E402
from app.bot.core.tool_executor import ToolExecutor  # noqa: E402
from app.bot.core.types import BotRequest  # noqa: E402
from app.bot.search.search_service import SearchResult  # noqa: E402


# ---------------------------------------------------------------------------
# Corpus loader (by id; we feed the role=='user' turn texts in order).
# ---------------------------------------------------------------------------

_CORPUS_PATH = (
    _REPO_ROOT
    / ".planning"
    / "phases"
    / "121-m6.3-audit-bot-recepcionista"
    / "raw"
    / "corpus.json"
)

# The 5 hardest corpus convs (OLD-bot known-failures).
_TARGET_CONVS = ["492", "168", "206", "355", "596"]

# Per-conv test phone (cleanup prefix +595981599xxx) + seeded source.
# 596 is telegram in corpus → replayed AS whatsapp (recepcionista is WA-only).
# 492 is infocasas in corpus but carries NO infocasas_ref (documented gap):
# seed source='infocasas' WITHOUT a ref.
_CONV_PLAN: dict[str, dict] = {
    "492": {"phone": "+595981599920", "source": "infocasas", "infocasas_ref": None},
    "168": {"phone": "+595981599921", "source": "whatsapp", "infocasas_ref": None},
    "206": {"phone": "+595981599922", "source": "whatsapp", "infocasas_ref": None},
    "355": {"phone": "+595981599923", "source": "whatsapp", "infocasas_ref": None},
    "596": {"phone": "+595981599924", "source": "whatsapp", "infocasas_ref": None},
}

# Cap inbound turns to keep the real-LLM run bounded (492 has 49 user turns).
# The recepcionista flujo (saludo → nombre → interés → derivación) resolves in
# the first handful of turns; replaying all 49 burns tokens without changing
# the OUTCOME proposition. Capped at 12 inbound turns per conv.
_MAX_INBOUND_TURNS = 12


def _load_corpus() -> dict[str, dict]:
    if not _CORPUS_PATH.exists():
        raise SystemExit(f"BLOCKER: corpus not present at {_CORPUS_PATH}")
    with _CORPUS_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return {c["conversation_id"]: c for c in data}


def _inbound_texts(conv: dict) -> list[str]:
    """Return the role=='user' turn texts in order (capped)."""
    texts = [
        (t.get("text") or "").strip()
        for t in conv.get("turns", [])
        if t.get("role") == "user" and (t.get("text") or "").strip()
    ]
    return texts[:_MAX_INBOUND_TURNS]


# ---------------------------------------------------------------------------
# Redaction (PII never printed raw — phone/email → shape only).
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# A LEAD-REF token like "LEAD-123" or "LEAD-ABC" — kept as a yes/no signal.
_LEADREF_RE = re.compile(r"\bLEAD[-_][A-Za-z0-9]+\b", re.IGNORECASE)


def _redact(text: str) -> str:
    """Redact phones/emails → shape tokens; keep everything else."""
    if not text:
        return ""
    out = _EMAIL_RE.sub(lambda m: f"<email:{len(m.group(0))}c>", text)
    out = _PHONE_RE.sub(lambda m: f"<phone:{len(m.group(0))}c>", out)
    return out


def _has_lead_ref(text: str) -> bool:
    return bool(_LEADREF_RE.search(text or ""))


# ---------------------------------------------------------------------------
# SearchService SPY — records every call, returns empty (a call = leak signal).
# ---------------------------------------------------------------------------

class _SearchSpy:
    """Record-and-return-empty stand-in for SearchService.

    In recepcionista mode the bot should NOT search. Any invocation is recorded
    so the scorer can flag an over-eager switch / leak. Returns zero properties
    so even if Claude does call search_properties, nothing leaks to the client.
    """

    def __init__(self) -> None:
        self.search_calls: list[dict] = []
        self.get_by_ids_calls: list = []
        # The real ToolExecutor reads search_service._geo_resolver for
        # resolver_zona / alternatives geo resolution.
        from app.bot.search.geo_resolver import GeoResolver

        self._geo_resolver = GeoResolver()

    async def search_properties(self, filters, session) -> SearchResult:
        try:
            dumped = filters.model_dump(exclude_none=True)
        except Exception:
            dumped = {"_unparsed": str(filters)[:200]}
        self.search_calls.append(dumped)
        return SearchResult(properties=[], total_found=0)

    async def get_by_ids(self, ids, session) -> SearchResult:
        self.get_by_ids_calls.append(list(ids))
        return SearchResult(properties=[], total_found=0)


# ---------------------------------------------------------------------------
# Per-conv result container.
# ---------------------------------------------------------------------------

@dataclass
class ConvResult:
    conv_id: str
    corpus_source: str
    flow: str
    test_phone: str
    contact_id: int | None = None
    contact_source: str | None = None
    contact_status: str | None = None
    partial_capture: bool = False
    visit_agent_user_id: int | None = None
    visit_source: str | None = None
    has_visit: bool = False
    search_invoked: bool = False
    search_calls: list = field(default_factory=list)
    leak_detected: bool = False
    lead_ref: str = "n-a"  # yes / no / n-a
    lead_registered: bool = False
    mode_switch_events: list = field(default_factory=list)
    outbound_redacted: list = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# DB helpers.
# ---------------------------------------------------------------------------

_DEV_DB_URL = (
    f"postgresql+asyncpg://{os.environ.get('POSTGRES_USER', 'onnix')}"
    f":{os.environ.get('POSTGRES_PASSWORD', '')}"
    f"@127.0.0.1:5432/onnix_dev"
)


async def _cleanup_phone(session: AsyncSession, phone: str) -> None:
    """Delete every seeded row for a test phone (mirror conftest cleanup)."""
    cid_subq = "(SELECT id FROM contacts WHERE phone = :phone)"
    # visits + lead_events + messages + conversations reference contact_id.
    for tbl in ("visits", "lead_events", "messages", "conversations"):
        await session.execute(
            sqlalchemy.text(
                f"DELETE FROM {tbl} WHERE contact_id IN {cid_subq}"
            ),
            {"phone": phone},
        )
    await session.execute(
        sqlalchemy.text("DELETE FROM contacts WHERE phone = :phone"),
        {"phone": phone},
    )
    await session.commit()


async def _seed_contact(session: AsyncSession, phone: str, source: str,
                        infocasas_ref: str | None) -> int:
    """Seed a contact with the corpus source BEFORE replay (idempotent)."""
    await _cleanup_phone(session, phone)
    await session.execute(
        sqlalchemy.text(
            "INSERT INTO contacts (name, phone, phone_normalized, source, "
            "status, infocasas_ref, created_at, last_activity_at) "
            "VALUES ('', :phone, :phone, :source, 'new', :ref, NOW(), NOW())"
        ),
        {"phone": phone, "source": source, "ref": infocasas_ref},
    )
    await session.commit()
    row = (
        await session.execute(
            sqlalchemy.text("SELECT id FROM contacts WHERE phone = :phone"),
            {"phone": phone},
        )
    ).first()
    return row.id


# ---------------------------------------------------------------------------
# Orchestrator builder — REAL everything except SearchService (spy).
# ---------------------------------------------------------------------------

def _build_orchestrator(search_spy: _SearchSpy) -> Orchestrator:
    claude = ClaudeClient(
        api_key=bot_settings.ANTHROPIC_API_KEY,
        model=bot_settings.CLAUDE_MODEL,
        timeout=float(bot_settings.BOT_TIMEOUT_SECONDS),
        max_retries=bot_settings.BOT_MAX_RETRIES,
    )
    # Gemini is the circuit-breaker fallback. We keep a stub: the breaker stays
    # closed (real Claude), so Gemini is not exercised. If Claude ever failed,
    # a real fallback would need a key; we leave the real client but never trip
    # the breaker. To stay deterministic + avoid a 2nd provider key dependency,
    # provide a harmless stub that raises if ever called (it should not be).
    gemini = AsyncMock()
    gemini.send_message = AsyncMock(
        side_effect=RuntimeError("Gemini fallback must NOT trigger in UAT")
    )

    circuit_breaker = CircuitBreaker(failure_threshold=3, reset_timeout=300)

    tool_executor = ToolExecutor(
        search_service=search_spy,
        alternatives_builder=None,
        bot_settings_repo=None,
    )

    orch = Orchestrator(
        claude=claude,
        gemini=gemini,
        circuit_breaker=circuit_breaker,
        search_service=search_spy,
        conversation_manager=ConversationManager(),
        response_builder=ResponseBuilder(),
        tool_executor=tool_executor,
        geo_data_path=bot_settings.GEO_DATA_PATH,
    )
    return orch


# ---------------------------------------------------------------------------
# Replay one conversation.
# ---------------------------------------------------------------------------

async def _replay_conv(conv_id: str, conv: dict, Session) -> ConvResult:
    plan = _CONV_PLAN[conv_id]
    phone = plan["phone"]
    md = conv.get("metadata", {})
    res = ConvResult(
        conv_id=conv_id,
        corpus_source=md.get("source", "?"),
        flow=md.get("flow_2_2", "?"),
        test_phone=phone,
    )

    inbounds = _inbound_texts(conv)
    if not inbounds:
        res.error = "no inbound user turns in corpus"
        return res

    search_spy = _SearchSpy()
    orch = _build_orchestrator(search_spy)

    # Seed the contact with the corpus source BEFORE replay so _resolve_mode
    # auto-detect + the source assertion are correct.
    async with Session() as seed_session:
        res.contact_id = await _seed_contact(
            seed_session, phone, plan["source"], plan["infocasas_ref"]
        )

    # Replay each inbound through the REAL orchestrator on a fresh session.
    # Patches keep all external chatter inside the process.
    with (
        patch(
            "app.bot.services.admin_notifier.get_admin_notifier",
            return_value=AsyncMock(),
        ),
        patch(
            "app.bot.core.orchestrator.get_opt_out_text",
            new=AsyncMock(return_value="Has solicitado la baja."),
        ),
        patch(
            "app.bot.ai.ai_dispatch.get_ai_dual_fail_text",
            new=AsyncMock(return_value="Error técnico."),
        ),
        patch("app.bot.core.orchestrator.set_request_context", return_value=None),
    ):
        for idx, text_in in enumerate(inbounds, start=1):
            request = BotRequest(
                platform="whatsapp",  # 596 forced to WA (recepcionista WA-only)
                chat_id=phone,
                user_id=phone,
                user_name="",
                text=text_in,
                external_id=f"uat_{conv_id}_{idx:04d}",
                callback_data=None,
            )
            async with Session() as turn_session:
                try:
                    response = await orch.handle_message(request, turn_session)
                    await turn_session.commit()
                except Exception as exc:  # capture, never crash the whole run
                    res.error = f"turn {idx}: {type(exc).__name__}: {exc}"
                    break

            if response is not None and response.text:
                res.outbound_redacted.append(
                    {
                        "turn": idx,
                        "intent": response.intent,
                        "is_lead": response.is_lead,
                        "len": len(response.text),
                        "props_in_payload": len(response.properties or []),
                        "lead_ref_present": _has_lead_ref(response.text),
                        "text_redacted": _redact(response.text),
                    }
                )

    # --- Collect DB outcomes from onnix_dev ---
    async with Session() as q:
        crow = (
            await q.execute(
                sqlalchemy.text(
                    "SELECT id, source, status FROM contacts WHERE phone = :phone"
                ),
                {"phone": phone},
            )
        ).first()
        if crow:
            res.contact_id = crow.id
            res.contact_source = crow.source
            res.contact_status = crow.status

        if res.contact_id is not None:
            # lead_registered + partial_capture + LEAD_REF
            lr = (
                await q.execute(
                    sqlalchemy.text(
                        "SELECT metadata FROM lead_events "
                        "WHERE contact_id = :cid AND event_type = 'lead_registered' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"cid": res.contact_id},
                )
            ).first()
            if lr is not None:
                res.lead_registered = True
                meta = lr.metadata
                if isinstance(meta, str):
                    meta = json.loads(meta)
                meta = meta or {}
                res.partial_capture = bool(meta.get("partial_capture", False))

            # mode_switch events
            ms_rows = (
                await q.execute(
                    sqlalchemy.text(
                        "SELECT metadata FROM lead_events "
                        "WHERE contact_id = :cid AND event_type = 'mode_switch' "
                        "ORDER BY created_at"
                    ),
                    {"cid": res.contact_id},
                )
            ).all()
            for r in ms_rows:
                meta = r.metadata
                if isinstance(meta, str):
                    meta = json.loads(meta)
                res.mode_switch_events.append(meta or {})

            # visits
            vrow = (
                await q.execute(
                    sqlalchemy.text(
                        "SELECT agent_user_id, source FROM visits "
                        "WHERE contact_id = :cid ORDER BY id DESC LIMIT 1"
                    ),
                    {"cid": res.contact_id},
                )
            ).first()
            if vrow is not None:
                res.has_visit = True
                res.visit_agent_user_id = vrow.agent_user_id
                res.visit_source = vrow.source

    # --- Derived signals ---
    res.search_calls = search_spy.search_calls
    res.search_invoked = bool(search_spy.search_calls or search_spy.get_by_ids_calls)
    # Leak = any outbound payload carried a property OR a search returned to
    # the client. The spy returns empty, so props_in_payload should be 0; a
    # search_invoked is the over-eager-switch signal.
    res.leak_detected = any(
        p["props_in_payload"] > 0 for p in res.outbound_redacted
    )
    # LEAD_REF: yes/no. All 5 corpus convs carry no committed LEAD_REF in the
    # OLD transcript; for the NEW bot, a derivation SHOULD mint one. Report the
    # observed signal: yes if any outbound carried a LEAD-REF token.
    res.lead_ref = "yes" if any(
        p["lead_ref_present"] for p in res.outbound_redacted
    ) else "no"

    return res


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

async def _amain() -> int:
    if not bot_settings.ANTHROPIC_API_KEY:
        print("BLOCKER: ANTHROPIC_API_KEY missing from environment/.env")
        return 2

    corpus = _load_corpus()
    engine = create_async_engine(_DEV_DB_URL, poolclass=NullPool, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    results: list[ConvResult] = []
    try:
        for conv_id in _TARGET_CONVS:
            conv = corpus.get(conv_id)
            if conv is None:
                r = ConvResult(conv_id=conv_id, corpus_source="?", flow="?",
                               test_phone=_CONV_PLAN[conv_id]["phone"],
                               error="corpus conv missing")
                results.append(r)
                continue
            print(f"\n{'='*70}\nReplaying conv {conv_id} "
                  f"(source={conv['metadata'].get('source')}, "
                  f"flow={conv['metadata'].get('flow_2_2')}, "
                  f"inbound_turns={len(_inbound_texts(conv))})\n{'='*70}",
                  flush=True)
            r = await _replay_conv(conv_id, conv, Session)
            results.append(r)
            _print_conv_result(r)
    finally:
        # Cleanup ALL seeded test phones, even on partial failure.
        async with Session() as cleanup:
            for conv_id in _TARGET_CONVS:
                await _cleanup_phone(cleanup, _CONV_PLAN[conv_id]["phone"])
        await engine.dispose()

    # Machine-readable summary block for the scorer (Task 3).
    print("\n\n===== MACHINE_SUMMARY_JSON =====")
    print(json.dumps([_machine_dict(r) for r in results], ensure_ascii=False, indent=2))
    print("===== END_MACHINE_SUMMARY_JSON =====")
    return 0


def _machine_dict(r: ConvResult) -> dict:
    return {
        "conv_id": r.conv_id,
        "corpus_source": r.corpus_source,
        "flow": r.flow,
        "test_phone": r.test_phone,
        "contact_id": r.contact_id,
        "contact_source": r.contact_source,
        "contact_status": r.contact_status,
        "partial_capture": r.partial_capture,
        "has_visit": r.has_visit,
        "visit_agent_user_id": r.visit_agent_user_id,
        "visit_source": r.visit_source,
        "search_invoked": r.search_invoked,
        "search_calls": r.search_calls,
        "leak_detected": r.leak_detected,
        "lead_ref": r.lead_ref,
        "lead_registered": r.lead_registered,
        "mode_switch_events": r.mode_switch_events,
        "n_outbound": len(r.outbound_redacted),
        "outbound_redacted": r.outbound_redacted,
        "error": r.error,
    }


def _print_conv_result(r: ConvResult) -> None:
    print(f"\n--- conv {r.conv_id} RESULT ---")
    print(f"  contact.source      : {r.contact_source}")
    print(f"  contact.status      : {r.contact_status}")
    print(f"  partial_capture     : {r.partial_capture}")
    print(f"  lead_registered     : {r.lead_registered}")
    print(f"  visit               : "
          f"{'agent=%r source=%r' % (r.visit_agent_user_id, r.visit_source) if r.has_visit else 'none'}")
    print(f"  search_invoked      : {r.search_invoked} "
          f"(calls={len(r.search_calls)})")
    print(f"  leak_detected       : {r.leak_detected}")
    print(f"  LEAD_REF            : {r.lead_ref}")
    print(f"  mode_switch events  : {len(r.mode_switch_events)} "
          f"{[m.get('reason') for m in r.mode_switch_events]}")
    print(f"  outbound turns      : {len(r.outbound_redacted)}")
    if r.error:
        print(f"  ERROR               : {r.error}")
    print("  --- outbound payloads (REDACTED) ---")
    for p in r.outbound_redacted:
        print(f"    [t{p['turn']}] intent={p['intent']} is_lead={p['is_lead']} "
              f"len={p['len']} props={p['props_in_payload']} "
              f"lead_ref={p['lead_ref_present']}")
        print(f"        {p['text_redacted']}")


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))

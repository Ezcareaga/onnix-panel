"""E2E — M6.3 BOT-14/BOT-15: switch-to-búsqueda guard (recepcionista mode).

This suite is the CALIBRATION harness for the automatic switch guard, driven
by ALL 28 labeled examples in the audit dataset ``switch_criteria.json``
(``.planning/phases/121-m6.3-audit-bot-recepcionista/raw/``, gitignored).

What the guard is
-----------------
The DECISION (switch / ask / continue) is a PROMPT guard living in
``RECEPCIONISTA_SYSTEM_PROMPT`` (123-04 §8). This plan (123-09) adds the
MECHANISM that the prompt's decision drives:

  - Decision A (switch_directo): Claude, from a recepcionista turn, calls
    ``search_properties`` with concrete DISTINCT criteria. The orchestrator
    must then make the switch STICKY (``search_context['mode']='busqueda'``)
    AND log a ``mode_switch`` lead_event with a reason in
    {zona_distinta, tipo_distinto, precio_fuera_rango}.
  - Decision B (preguntar_criterios): Claude asks 1-2 criteria, no tool call.
    Mode stays recepcionista; NO mode_switch event.
  - Decision C (no_switch): Claude continues the recepcionista flow on the same
    prop, no search. Mode stays recepcionista; NO mode_switch event.

Why Claude is scripted per label
--------------------------------
Claude is MOCKED here (ConversationRunner) — there is no live LLM and the
prompt-level classification is exercised in 123-04's prompt tests. This suite
calibrates the MECHANISM: for each labeled example we script Claude's decision
to match the label (switch_directo → a ``search_properties`` call carrying the
example's distinct criteria; preguntar/no_switch → a text reply), then assert
the orchestrator's resulting state (sticky flip + log on A; no flip / no log on
B and C). This is a CLASS-LEVEL assertion (matches the label), not exact text.

Dataset composition (28 examples, 3 classes)
--------------------------------------------
  switch_directo      12  (zona_distinta 5 / tipo_distinto 5 / precio_fuera_rango 2)
  preguntar_criterios  9  (duda_generica 8 / rechazo_explicito 1)
  no_switch            7  (continua_misma_prop 7)

THIN SPOTS (flagged, NOT fabricated)
------------------------------------
  - ``rechazo_explicito``  has only **1** example (pc_s04). One data point —
    not enough to generalize the rechazo sub-bucket; it is asserted under the
    same preguntar_criterios contract (ask, no switch).
  - ``precio_fuera_rango`` has only **2** examples (sw_r07, sw_s03). Two data
    points for the price-distinct switch reason; both asserted, but the reason
    enum is under-sampled relative to zona/tipo (5 each).

External surfaces (Claude, SearchService, channel senders) are mocked via
``ConversationRunner``; no network, no live LLM. DB writes go to onnix_dev
within the test cleanup phone range.
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

from app.bot.core.types import (
    ConversationState,
    ContactInfo,
    ConversationInfo,
    HistoryMessage,
)
from tests.bot.e2e.runner import (
    ConversationRunner,
    _make_text_ai_response,
    _make_tool_ai_response,
)


# ---------------------------------------------------------------------------
# Dataset loader — the 28 labeled switch examples (gitignored corpus).
# ---------------------------------------------------------------------------

_SWITCH_CRITERIA_PATH = (
    Path(_panel_dir).parent
    / ".planning"
    / "phases"
    / "121-m6.3-audit-bot-recepcionista"
    / "raw"
    / "switch_criteria.json"
)

_VALID_REASONS = {"zona_distinta", "tipo_distinto", "precio_fuera_rango"}


def _load_switch_criteria() -> list[dict]:
    """Return the 28 labeled switch examples.

    SKIPS when the dataset is absent — it is gitignored and may not be present
    in every checkout.
    """
    if not _SWITCH_CRITERIA_PATH.exists():
        pytest.skip(f"switch_criteria dataset not present at {_SWITCH_CRITERIA_PATH}")
    with _SWITCH_CRITERIA_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


_EXAMPLES = _load_switch_criteria() if _SWITCH_CRITERIA_PATH.exists() else []
_EXAMPLE_IDS = [ex["example_id"] for ex in _EXAMPLES]


def _ejemplo(example_id: str) -> dict:
    """Devuelve un ejemplo del dataset, o skipea si el dataset no esta.

    Los tests parametrizados ya skipean solos cuando falta el dataset —esta
    gitignored y no esta en todos los checkouts—, pero los dos anchors con
    nombre lo buscaban con un generador sin default sobre una lista vacia. Eso
    levanta StopIteration adentro de una corrutina, que asyncio convierte en
    `RuntimeError: coroutine raised StopIteration`: dos rojos cuyo mensaje no
    menciona el archivo que falta.
    """
    if not _EXAMPLES:
        pytest.skip(f"switch_criteria dataset not present at {_SWITCH_CRITERIA_PATH}")
    for ex in _EXAMPLES:
        if ex["example_id"] == example_id:
            return ex
    pytest.fail(f"el dataset no tiene el ejemplo {example_id}")


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
async def switch_contact(db_session: AsyncSession):
    """Seed a plain WhatsApp lead in recepcionista mode (a consulted-prop turn)."""
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
            "VALUES ('Switch E2E User', :phone, 'whatsapp', 'new')"
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


# ---------------------------------------------------------------------------
# Helpers — build a recepcionista runner + script Claude per label.
# ---------------------------------------------------------------------------

def _make_runner(
    db_session: AsyncSession,
    contact: dict,
    example: dict,
    monkeypatch,
) -> ConversationRunner:
    """Recepcionista-mode runner seeded with the example's prior turns."""
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
            name="Switch E2E User",
            status="new",
            is_baja=False,
            platform="whatsapp",
            phone=contact["phone"],
            source_id=contact["phone"],
            source="whatsapp",
            infocasas_ref=None,
        )
    )
    # Force recepcionista via _resolve_mode check 1 (per-chat override). A
    # last_detalle_id models the consulted-prop context the dataset assumes.
    runner.set_search_context(
        ConversationState(mode="recepcionista", last_detalle_id=999001)
    )
    runner.set_conversation(
        ConversationInfo(
            id=765490,
            contact_id=contact["id"],
            platform="whatsapp",
            chat_id=contact["phone"],
            is_bot_active=True,
        )
    )
    # Replay the example's prior turns into history.
    history: list[HistoryMessage] = []
    for turn in example.get("context_prev_turns", []):
        if turn["role"] == "bot":
            history.append(
                HistoryMessage(direction="outbound", sender_type="bot", body=turn["text"])
            )
        else:
            history.append(
                HistoryMessage(direction="inbound", sender_type="contact", body=turn["text"])
            )
    runner.set_history(history)
    return runner


# Distinct-criteria filter scripts per switch reason. These mirror what Claude
# WOULD pass to search_properties when the prompt decides "switch" — distinct
# zona / tipo / precio relative to the consulted prop. The orchestrator derives
# the mode_switch reason from these filters.
def _switch_filters_for_reason(reason: str) -> dict:
    if reason == "zona_distinta":
        return {"operacion": "venta", "ciudad": "Lambaré", "barrio": "centro"}
    if reason == "tipo_distinto":
        return {"operacion": "alquiler", "tipo": "departamento"}
    if reason == "precio_fuera_rango":
        return {"operacion": "venta", "precio_max": 200000000, "moneda": "PYG"}
    # Defensive: a switch with no clean reason still carries some criteria.
    return {"operacion": "venta", "ciudad": "Asunción"}


def _script_switch(runner: ConversationRunner, reason: str) -> None:
    """Script Claude to make decision A: call search_properties (switch)."""
    filters = _switch_filters_for_reason(reason)
    search_result = {
        "properties": [{"id": 990011, "title": "Prop switch", "city": "Lambaré"}],
        "total_found": 1,
        "all_ids": [990011],
    }
    runner.program_tool_executor_result(search_result)
    search_tool = _make_tool_ai_response("search_properties", filters)
    final_text = _make_text_ai_response("Encontré opciones con esos criterios.")
    runner._claude_mock.send_message.side_effect = [search_tool, final_text]
    runner._last_tool_name = "search_properties"
    runner._last_tool_args = filters


def _script_no_search(runner: ConversationRunner, text: str) -> None:
    """Script Claude to make decision B/C: a text reply, no tool call."""
    runner._claude_mock.send_message.side_effect = None
    runner._claude_mock.send_message.return_value = _make_text_ai_response(text)
    runner._last_tool_name = "none"
    runner._last_tool_args = {}


async def _fetch_mode_switch_events(db_session: AsyncSession, contact_id: int) -> list[dict]:
    """Return all mode_switch lead_events (metadata parsed) for a contact."""
    rows = (
        await db_session.execute(
            sqlalchemy.text(
                "SELECT metadata FROM lead_events "
                "WHERE contact_id = :cid AND event_type = 'mode_switch' "
                "ORDER BY created_at"
            ),
            {"cid": contact_id},
        )
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        meta = r.metadata
        if isinstance(meta, str):
            meta = json.loads(meta)
        out.append(meta or {})
    return out


# ---------------------------------------------------------------------------
# The calibration test — parametrized over all 28 examples.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("example", _EXAMPLES, ids=_EXAMPLE_IDS)
async def test_switch_guard(example, db_session, switch_contact, monkeypatch):
    """Class-level calibration: bot DECISION matches the label for all 28.

    switch_directo      → switch: search_properties usable + mode flips to
                          'busqueda' (sticky) + mode_switch event with reason.
    preguntar_criterios → ask: no search, mode stays recepcionista, no log.
    no_switch           → continue same prop: no search, mode stays, no log.
    """
    label = example["label"]
    reason = example.get("reason", "")
    runner = _make_runner(db_session, switch_contact, example, monkeypatch)

    if label == "switch_directo":
        _script_switch(runner, reason)
        await runner.send(example["message_text"])

        # Sticky flip: mode override now 'busqueda' (check 1 of §3 wins after).
        assert runner.context.get_mode_override() == "busqueda", (
            f"[{example['example_id']}] switch_directo must flip "
            f"search_context['mode'] to 'busqueda' (sticky). "
            f"Got: {runner.context.get_mode_override()!r}"
        )
        # mode_switch lead_event logged with a valid reason.
        events = await _fetch_mode_switch_events(db_session, switch_contact["id"])
        assert len(events) >= 1, (
            f"[{example['example_id']}] switch_directo must log a mode_switch "
            f"lead_event. Found none."
        )
        meta = events[-1]
        assert meta.get("from") == "recepcionista" and meta.get("to") == "busqueda", (
            f"[{example['example_id']}] mode_switch metadata must record "
            f"from=recepcionista to=busqueda. Got: {meta!r}"
        )
        assert meta.get("reason") in _VALID_REASONS, (
            f"[{example['example_id']}] mode_switch reason must be in "
            f"{_VALID_REASONS}. Got: {meta.get('reason')!r}"
        )

    elif label in ("preguntar_criterios", "no_switch"):
        _script_no_search(
            runner,
            "¿Buscás en otra zona, otro tipo de propiedad o con otro presupuesto?"
            if label == "preguntar_criterios"
            else "Te paso más detalles de esa misma propiedad.",
        )
        await runner.send(example["message_text"])

        # No switch: mode stays recepcionista (no sticky flip).
        assert runner.context.get_mode_override() == "recepcionista", (
            f"[{example['example_id']}] {label} must NOT switch — mode must "
            f"stay 'recepcionista'. Got: {runner.context.get_mode_override()!r}"
        )
        # No mode_switch event logged.
        events = await _fetch_mode_switch_events(db_session, switch_contact["id"])
        assert events == [], (
            f"[{example['example_id']}] {label} must NOT log a mode_switch "
            f"event. Found: {events!r}"
        )

    else:  # pragma: no cover — dataset only has the 3 known classes.
        pytest.fail(f"[{example['example_id']}] unexpected label {label!r}")


# ---------------------------------------------------------------------------
# Named BOT-14 / BOT-15 anchors (explicit, in addition to the parametrization).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_switch_automatico_con_criterios_distintos(
    db_session, switch_contact, monkeypatch
):
    """BOT-14: concrete distinct criteria → switch (sticky flip + logged reason).

    Anchored on a switch_directo example (sw_r01 — zona_distinta). Asserts the
    full switch contract AND that the switch is sticky on a FOLLOW-UP turn (the
    mode override survives into the next turn).
    """
    example = _ejemplo("sw_r01")
    runner = _make_runner(db_session, switch_contact, example, monkeypatch)

    _script_switch(runner, example["reason"])
    await runner.send(example["message_text"])

    assert runner.context.get_mode_override() == "busqueda"
    events = await _fetch_mode_switch_events(db_session, switch_contact["id"])
    assert len(events) == 1
    assert events[0].get("reason") in _VALID_REASONS

    # Sticky proof: a SECOND turn re-uses the persisted override. We feed the
    # flipped context back in (as the conversation_manager would) and a plain
    # text reply; mode must remain 'busqueda' and NO new mode_switch is logged.
    runner.set_search_context(
        ConversationState(mode="busqueda", last_detalle_id=999001)
    )
    _script_no_search(runner, "Seguimos en búsqueda. ¿Algo más?")
    await runner.send("dale, mostrame")
    events_after = await _fetch_mode_switch_events(db_session, switch_contact["id"])
    assert len(events_after) == 1, (
        "sticky switch must not re-log mode_switch on subsequent busqueda turns"
    )


@pytest.mark.asyncio
async def test_no_switch_cuando_solo_dice_que_mas_tenes(
    db_session, switch_contact, monkeypatch
):
    """BOT-15: vague doubt ("¿Qué más tenés?") → ask, NO switch, NO log.

    Anchored on pc_s01 (preguntar_criterios / duda_generica). The bot asks for
    criteria; mode stays recepcionista; no mode_switch event.
    """
    example = _ejemplo("pc_s01")
    runner = _make_runner(db_session, switch_contact, example, monkeypatch)

    _script_no_search(
        runner, "¿Buscás otra zona, otro tipo o con otro presupuesto?"
    )
    await runner.send(example["message_text"])

    assert runner.context.get_mode_override() == "recepcionista"
    events = await _fetch_mode_switch_events(db_session, switch_contact["id"])
    assert events == [], "preguntar_criterios must not log a mode_switch event"


# ---------------------------------------------------------------------------
# Dataset integrity guard — the 28-example composition is what we calibrate to.
# ---------------------------------------------------------------------------

def test_dataset_composition_is_28_examples_3_classes():
    """Lock the calibration dataset shape; flag the thin sub-buckets.

    THIN SPOTS (see module docstring): rechazo_explicito=1, precio_fuera_rango=2.
    """
    examples = _load_switch_criteria()
    assert len(examples) == 28, f"expected 28 examples, got {len(examples)}"

    by_label: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for ex in examples:
        by_label[ex["label"]] = by_label.get(ex["label"], 0) + 1
        by_reason[ex["reason"]] = by_reason.get(ex["reason"], 0) + 1

    assert by_label == {
        "switch_directo": 12,
        "preguntar_criterios": 9,
        "no_switch": 7,
    }, f"class composition drifted: {by_label}"

    # Thin spots flagged explicitly (asserted so drift is caught, not hidden).
    assert by_reason.get("rechazo_explicito") == 1, "thin spot rechazo_explicito"
    assert by_reason.get("precio_fuera_rango") == 2, "thin spot precio_fuera_rango"

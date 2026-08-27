"""E2E — M6.3 BOT-16/BOT-17: vista_publica handshake (public-site CTA).

The public site (onnix.com.py) offers a "consultar por WhatsApp" CTA that
pre-fills the message::

    "Hola! Me interesa la propiedad {CODIGO_PROP} que vi en onnix.com.py"

When a NEW contact arrives with that CTA, the bot must:

  1. DETECT the handshake (``_is_vista_publica_handshake``) — tolerant to
     lowercase domain, extra spaces, www./https:// prefixes, trailing slash,
     code-then-domain order, and a minor typo near the domain.
  2. EXTRACT the prop code (``_extract_prop_code``) — the corpus codes are
     6-char alphanumeric tokens (e.g. EC1754, A99D31, GAEAE3).
  3. CREATE the contact with ``source='vista_publica'`` and store the code on
     ``contact.infocasas_ref`` so the DIRECTO greeting references the prop
     (infocasas_ref reused as the prop-code carrier — semantic stretch).
  4. RESOLVE mode to 'recepcionista' (auto-detect check 2: source and/or
     infocasas_ref).

The detector + extractor are pure functions (unit-tested directly). The
source branch is exercised against the REAL ``ConversationManager.resolve_contact``
on onnix_dev (test phone within the cleanup range). Mode resolution is
exercised against the REAL ``Orchestrator._resolve_mode``. No live LLM /
network.
"""
from __future__ import annotations

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

from app.bot.core.conversation import (  # noqa: E402
    ConversationManager,
    _extract_prop_code,
    _is_vista_publica_handshake,
)
from app.bot.core.orchestrator import Orchestrator  # noqa: E402
from app.bot.core.types import (  # noqa: E402
    BotRequest,
    ConversationState,
)


# ---------------------------------------------------------------------------
# CTA variants — the property code is EC1754 in every positive case.
# ---------------------------------------------------------------------------

_CODE = "EC1754"

# Each tuple: (label, text, expected_code).
_POSITIVE_VARIANTS = [
    (
        "exact_cta",
        "Hola! Me interesa la propiedad EC1754 que vi en onnix.com.py",
        "EC1754",
    ),
    (
        "lowercase_domain",
        "hola, me interesa la propiedad ec1754 que vi en ONNIX.COM",
        "EC1754",
    ),
    (
        "extra_spaces",
        "Hola!   Me interesa la  propiedad   EC1754   que vi en   onnix.com.py ",
        "EC1754",
    ),
    (
        "www_prefix",
        "Me interesa la propiedad EC1754 que vi en www.onnix.com.py",
        "EC1754",
    ),
    (
        "https_prefix",
        "Me interesa la propiedad EC1754 que vi en https://onnix.com.py/",
        "EC1754",
    ),
    (
        "code_then_domain",
        "Vi EC1754 en onnix.com.py y me interesa, mas info?",
        "EC1754",
    ),
    (
        "minor_typo_domain",
        "Me interesa la propiedad EC1754 que vi en onnix.con",
        "EC1754",
    ),
]

# Real corpus code shapes (6-char alphanumeric) must extract too.
_CORPUS_CODES = ["A99D31", "GAEAE3", "B651EC", "EC1754"]

_NEGATIVE_VARIANTS = [
    ("plain_greeting", "Hola, buenas tardes, queria consultar"),
    ("search_no_domain", "Busco una casa de 3 dormitorios en Lambare"),
    ("domain_no_code", "Vi su pagina onnix.com.py, tienen departamentos?"),
]


# ---------------------------------------------------------------------------
# Pure-function unit tests: detector + extractor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,text,expected", _POSITIVE_VARIANTS)
def test_handshake_detected_on_variants(label, text, expected):
    """Every CTA variant is detected as a vista_publica handshake."""
    assert _is_vista_publica_handshake(text), (
        f"variant {label!r} should be detected as a handshake: {text!r}"
    )


@pytest.mark.parametrize("label,text,expected", _POSITIVE_VARIANTS)
def test_prop_code_extracted_on_variants(label, text, expected):
    """The prop code is extracted correctly from every CTA variant."""
    assert _extract_prop_code(text) == expected, (
        f"variant {label!r}: expected code {expected!r} from {text!r}, "
        f"got {_extract_prop_code(text)!r}"
    )


@pytest.mark.parametrize("code", _CORPUS_CODES)
def test_prop_code_extracted_real_corpus_shapes(code):
    """Real 6-char alphanumeric corpus codes extract from the CTA template."""
    text = f"Hola! Me interesa la propiedad {code} que vi en onnix.com.py"
    assert _is_vista_publica_handshake(text)
    assert _extract_prop_code(text) == code


@pytest.mark.parametrize("label,text", _NEGATIVE_VARIANTS)
def test_not_a_handshake(label, text):
    """Ordinary messages (no domain+code pair) are NOT handshakes."""
    assert not _is_vista_publica_handshake(text), (
        f"variant {label!r} must NOT be detected as a handshake: {text!r}"
    )


def test_handshake_none_text():
    """None / empty text is never a handshake (no crash)."""
    assert not _is_vista_publica_handshake(None)
    assert not _is_vista_publica_handshake("")
    assert _extract_prop_code(None) is None
    assert _extract_prop_code("") is None


# ---------------------------------------------------------------------------
# DB engine (NullPool) — mirrors test_flujo_directo_ic.py.
# ---------------------------------------------------------------------------

_DEV_DB_URL = (
    f"postgresql+asyncpg://{os.environ.get('POSTGRES_USER', 'onnix')}"
    f":{os.environ.get('POSTGRES_PASSWORD', '')}"
    f"@127.0.0.1:5432/onnix_dev"
)

_engine = create_async_engine(_DEV_DB_URL, poolclass=NullPool, echo=False)
_Session = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

# Test phone within the cleanup range (+595981[5-9]...).
_TEST_PHONE = "+595981599931"


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
async def clean_contact(db_session: AsyncSession):
    """Ensure no leftover contact for the test phone, before and after."""
    async def _cleanup():
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
        await db_session.commit()

    await _cleanup()
    yield _TEST_PHONE
    await _cleanup()


# ---------------------------------------------------------------------------
# resolve_contact source branch — REAL ConversationManager against dev DB.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_contact_sets_vista_publica_source(db_session, clean_contact):
    """A NEW WhatsApp contact whose first message is a CTA handshake is created
    with source='vista_publica' and infocasas_ref=<extracted code>."""
    cm = ConversationManager()
    text_msg = "Hola! Me interesa la propiedad EC1754 que vi en onnix.com.py"

    contact = await cm.resolve_contact(
        db_session,
        platform="whatsapp",
        user_id=clean_contact,
        user_name="Visitante Web",
        text_msg=text_msg,
    )
    await db_session.commit()

    assert contact.source == "vista_publica", (
        f"NEW handshake contact must have source='vista_publica', "
        f"got {contact.source!r}"
    )
    assert contact.infocasas_ref == "EC1754", (
        f"extracted prop code must be stored on infocasas_ref, "
        f"got {contact.infocasas_ref!r}"
    )

    # Confirm persisted to DB (not just the returned dataclass).
    row = (
        await db_session.execute(
            sqlalchemy.text(
                "SELECT source, infocasas_ref FROM contacts WHERE phone = :phone"
            ),
            {"phone": clean_contact},
        )
    ).first()
    assert row.source == "vista_publica"
    assert row.infocasas_ref == "EC1754"


@pytest.mark.asyncio
async def test_resolve_contact_non_handshake_keeps_whatsapp_source(
    db_session, clean_contact
):
    """A NEW contact whose first message is NOT a handshake keeps source='whatsapp'
    and does not get a spurious infocasas_ref."""
    cm = ConversationManager()
    contact = await cm.resolve_contact(
        db_session,
        platform="whatsapp",
        user_id=clean_contact,
        user_name="Cliente Normal",
        text_msg="Busco una casa de 3 dormitorios en Lambare",
    )
    await db_session.commit()

    assert contact.source == "whatsapp"
    assert not contact.infocasas_ref


@pytest.mark.asyncio
async def test_resolve_contact_existing_contact_not_relabeled(
    db_session, clean_contact
):
    """An EXISTING contact is NOT relabeled to vista_publica on a later CTA
    message (source branch only applies to NEW contacts)."""
    cm = ConversationManager()
    # First touch: ordinary message → source='whatsapp'.
    await cm.resolve_contact(
        db_session, platform="whatsapp", user_id=clean_contact,
        user_name="Cliente", text_msg="Hola",
    )
    await db_session.commit()
    # Second touch: a CTA handshake. Contact already exists → must stay 'whatsapp'.
    contact = await cm.resolve_contact(
        db_session, platform="whatsapp", user_id=clean_contact,
        user_name="Cliente",
        text_msg="Hola! Me interesa la propiedad EC1754 que vi en onnix.com.py",
    )
    await db_session.commit()
    assert contact.source == "whatsapp", (
        "existing contact must not be relabeled to vista_publica"
    )


# ---------------------------------------------------------------------------
# Mode resolution — REAL Orchestrator._resolve_mode.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handshake_resolves_to_recepcionista(db_session, clean_contact):
    """A vista_publica handshake (NEW contact) resolves to recepcionista mode
    via auto-detect (check 2)."""
    cm = ConversationManager()
    contact = await cm.resolve_contact(
        db_session, platform="whatsapp", user_id=clean_contact,
        user_name="Visitante Web",
        text_msg="Hola! Me interesa la propiedad EC1754 que vi en onnix.com.py",
    )
    await db_session.commit()

    orch = Orchestrator(
        conversation_manager=AsyncMock(),
        response_builder=None,
        tool_executor=AsyncMock(),
    )
    request = BotRequest(
        platform="whatsapp",
        chat_id=clean_contact,
        user_id=clean_contact,
        user_name="Visitante Web",
        text="Hola! Me interesa la propiedad EC1754 que vi en onnix.com.py",
        external_id="msg_handshake_001",
    )
    mode = await orch._resolve_mode(
        request, contact, ConversationState(), db_session
    )
    assert mode == "recepcionista", (
        f"vista_publica handshake must resolve to recepcionista, got {mode!r}"
    )

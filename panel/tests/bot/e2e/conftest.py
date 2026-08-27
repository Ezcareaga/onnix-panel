"""Shared fixtures for E2E bot tests.

All fixtures use onnix_dev — NEVER production.
Test phone prefix: +5959815999xx — within the test cleanup range.
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
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)

# Ensure panel/ is on sys.path before any app import
_panel_dir = str(Path(__file__).resolve().parent.parent.parent.parent)
if _panel_dir not in sys.path:
    sys.path.insert(0, _panel_dir)

# Force dev DB and silence external services BEFORE any app import
os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ.setdefault("POSTGRES_DB", "onnix_dev")
os.environ["TELEGRAM_EZ_CHAT_ID"] = ""
os.environ["FOLLOWUP_SENDER_ENABLED"] = "false"

from app.bot.search.search_service import SearchResult
from tests.bot.e2e.runner import ConversationRunner


# ---------------------------------------------------------------------------
# DB engine (NullPool — fresh connection per test)
# ---------------------------------------------------------------------------

_DEV_DB_URL = (
    f"postgresql+asyncpg://{os.environ.get('POSTGRES_USER', 'onnix')}"
    f":{os.environ.get('POSTGRES_PASSWORD', '')}"
    f"@127.0.0.1:5432/onnix_dev"
)

_e2e_engine = create_async_engine(_DEV_DB_URL, poolclass=NullPool, echo=False)
_E2ESession = async_sessionmaker(_e2e_engine, class_=AsyncSession, expire_on_commit=False)

# Test phone — within TEST_PHONE_PREFIX_SQL cleanup range (+595981[5-9]...)
_TEST_PHONE = "+595981599901"


# ---------------------------------------------------------------------------
# DB session fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def e2e_session() -> AsyncSession:
    """Async session connected to onnix_dev with NullPool."""
    session = _E2ESession()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Seeded contact fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def seeded_contact(e2e_session: AsyncSession) -> dict:
    """Create a test contact in onnix_dev and clean up after the test.

    Uses phone +595981599901 which falls in the test cleanup range
    (TEST_PHONE_PREFIX_SQL in the root conftest.py).
    """
    # Delete any leftover contact with this phone first (idempotent setup)
    await e2e_session.execute(
        sqlalchemy.text(
            "DELETE FROM lead_events WHERE contact_id IN "
            "(SELECT id FROM contacts WHERE phone = :phone)"
        ),
        {"phone": _TEST_PHONE},
    )
    await e2e_session.execute(
        sqlalchemy.text(
            "DELETE FROM messages WHERE contact_id IN "
            "(SELECT id FROM contacts WHERE phone = :phone)"
        ),
        {"phone": _TEST_PHONE},
    )
    await e2e_session.execute(
        sqlalchemy.text(
            "DELETE FROM conversations WHERE contact_id IN "
            "(SELECT id FROM contacts WHERE phone = :phone)"
        ),
        {"phone": _TEST_PHONE},
    )
    await e2e_session.execute(
        sqlalchemy.text("DELETE FROM contacts WHERE phone = :phone"),
        {"phone": _TEST_PHONE},
    )
    await e2e_session.execute(
        sqlalchemy.text(
            "INSERT INTO contacts (name, phone, source, status) "
            "VALUES ('E2E Test User', :phone, 'test', 'new')"
        ),
        {"phone": _TEST_PHONE},
    )
    await e2e_session.commit()

    result = await e2e_session.execute(
        sqlalchemy.text(
            "SELECT id, name, phone, status FROM contacts WHERE phone = :phone"
        ),
        {"phone": _TEST_PHONE},
    )
    row = result.first()
    contact = {"id": row.id, "name": row.name, "phone": row.phone, "status": row.status}

    yield contact

    # Cleanup: remove messages, lead_events, conversations, then contact
    await e2e_session.execute(
        sqlalchemy.text(
            "DELETE FROM lead_events WHERE contact_id = :cid"
        ),
        {"cid": contact["id"]},
    )
    await e2e_session.execute(
        sqlalchemy.text(
            "DELETE FROM messages WHERE contact_id = :cid"
        ),
        {"cid": contact["id"]},
    )
    await e2e_session.execute(
        sqlalchemy.text(
            "DELETE FROM conversations WHERE contact_id = :cid"
        ),
        {"cid": contact["id"]},
    )
    await e2e_session.execute(
        sqlalchemy.text(
            "DELETE FROM contacts WHERE phone = :phone"
        ),
        {"phone": _TEST_PHONE},
    )
    await e2e_session.commit()


# ---------------------------------------------------------------------------
# Seeded properties fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def seeded_properties(e2e_session: AsyncSession) -> list[dict]:
    """Return 3-5 real active properties from onnix_dev.

    Queries existing data rather than inserting, so no cleanup needed.
    Falls back to mock dicts if the DB has no active properties.
    """
    result = await e2e_session.execute(
        sqlalchemy.text(
            "SELECT id, title, operation_type, property_type, city, "
            "price_usd FROM properties "
            "WHERE is_active = true AND is_duplicate = false "
            "AND city ILIKE '%asunci%' "
            "LIMIT 5"
        )
    )
    rows = result.fetchall()

    if rows:
        return [
            {
                "id": r.id,
                "title": r.title,
                "operation": r.operation_type,
                "property_type": r.property_type,
                "city": r.city,
                "price_usd": float(r.price_usd) if r.price_usd else None,
                "is_active": True,
                "source": "onnix",
                "local_image_count": 1,
            }
            for r in rows
        ]

    # Deterministic fallback when DB has no matching data
    return [
        {
            "id": 90001 + i,
            "title": f"Propiedad E2E test {i + 1}",
            "operation": "venta",
            "property_type": "casa",
            "city": "Asuncion",
            "price_usd": 150000 + i * 10000,
            "is_active": True,
            "source": "onnix",
            "local_image_count": 2,
        }
        for i in range(3)
    ]


# ---------------------------------------------------------------------------
# Mock fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def claude_mock() -> AsyncMock:
    """AsyncMock for ClaudeClient.send_message, ready to be programmed."""
    mock = AsyncMock()
    mock.send_message = AsyncMock()
    return mock


@pytest.fixture
def search_mock() -> AsyncMock:
    """AsyncMock for SearchService with search_properties and get_by_ids."""
    mock = AsyncMock()
    mock.search_properties = AsyncMock(
        return_value=SearchResult(properties=[], total_found=0)
    )
    mock.get_by_ids = AsyncMock(
        return_value=SearchResult(properties=[], total_found=0)
    )
    return mock


# ---------------------------------------------------------------------------
# ConversationRunner fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def runner(
    e2e_session: AsyncSession,
    claude_mock: AsyncMock,
    search_mock: AsyncMock,
    seeded_contact: dict,
    monkeypatch,
) -> ConversationRunner:
    """ConversationRunner wired with real session + mocked Claude/Search.

    The runner's conversation_manager is pre-wired to use the seeded_contact
    id so DB writes go to an identifiable test record.

    The ``is_bot_active`` gate is patched to always return True for E2E
    tests — the mocked ``conversation_manager`` fabricates a ConversationInfo
    that is never persisted, so the real ``check_bot_active_locked`` DB query
    would return None (treated as False) and every message would be dropped
    at the gate. Tests that want to exercise the inactive path should
    override this patch locally.
    """
    monkeypatch.setattr(
        "app.bot.core.orchestrator.check_bot_active_locked",
        AsyncMock(return_value=True),
    )
    r = ConversationRunner(
        session=e2e_session,
        claude_mock=claude_mock,
        search_mock=search_mock,
        platform="whatsapp",
        chat_id=_TEST_PHONE,
        contact_id=seeded_contact["id"],
        conversation_id=None,
    )
    return r

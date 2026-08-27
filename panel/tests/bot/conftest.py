"""Shared fixtures for bot tests.

All bot tests run against onnix_dev — NEVER production.
"""
import os
import sys
from pathlib import Path

# Force dev database BEFORE any app import
os.environ["POSTGRES_HOST"] = "127.0.0.1"
os.environ.setdefault("POSTGRES_DB", "onnix_dev")

# Ensure panel/ is on sys.path
_panel_dir = str(Path(__file__).resolve().parent.parent.parent)
if _panel_dir not in sys.path:
    sys.path.insert(0, _panel_dir)

import pytest
import pytest_asyncio
import sqlalchemy
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)

# ---------------------------------------------------------------------------
# Dev DB engine — NullPool for test isolation
# ---------------------------------------------------------------------------

_DEV_DB_URL = (
    f"postgresql+asyncpg://{os.environ.get('POSTGRES_USER', 'onnix')}"
    f":{os.environ.get('POSTGRES_PASSWORD', '')}"
    f"@127.0.0.1:5432/onnix_dev"
)

_test_engine = create_async_engine(_DEV_DB_URL, poolclass=NullPool, echo=False)
_TestSession = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Async DB session connected to onnix_dev."""
    session = _TestSession()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@pytest_asyncio.fixture
async def sample_contact(db_session: AsyncSession) -> dict:
    """A test contact from the dev database."""
    result = await db_session.execute(
        sqlalchemy.text(
            "SELECT id, name, phone, status FROM contacts LIMIT 1"
        )
    )
    row = result.first()
    if row:
        return {
            "id": row.id, "name": row.name,
            "phone": row.phone, "status": row.status,
        }
    await db_session.execute(
        sqlalchemy.text(
            "INSERT INTO contacts (name, phone, source, status) "
            "VALUES ('Bot Test User', '+595981000001', 'test', 'new') "
            "ON CONFLICT (phone) DO UPDATE SET name = 'Bot Test User' "
            "RETURNING id, name, phone, status"
        )
    )
    await db_session.commit()
    result = await db_session.execute(
        sqlalchemy.text(
            "SELECT id, name, phone, status FROM contacts "
            "WHERE phone = '+595981000001'"
        )
    )
    row = result.first()
    return {
        "id": row.id, "name": row.name,
        "phone": row.phone, "status": row.status,
    }


@pytest_asyncio.fixture
async def sample_properties(db_session: AsyncSession) -> list[dict]:
    """5 real properties from the dev database."""
    result = await db_session.execute(
        sqlalchemy.text(
            "SELECT id, title, operation_type, property_type, city, "
            "price_usd FROM properties "
            "WHERE is_active = true AND is_duplicate = false LIMIT 5"
        )
    )
    rows = result.fetchall()
    return [
        {
            "id": r.id, "title": r.title,
            "operation_type": r.operation_type,
            "property_type": r.property_type,
            "city": r.city,
            "price_usd": float(r.price_usd) if r.price_usd else None,
        }
        for r in rows
    ]


@pytest.fixture
def mock_claude_response() -> dict:
    """Mock Claude API response (text only, no tool use)."""
    return {
        "id": "msg_test_001",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Hola! Soy el asistente de Onnix."}
        ],
        "model": "claude-haiku-4-5-20251001",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 100, "output_tokens": 25},
    }


@pytest.fixture
def mock_claude_tool_use() -> dict:
    """Mock Claude API response with tool_use (search_properties)."""
    return {
        "id": "msg_test_002",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_test_001",
                "name": "search_properties",
                "input": {
                    "operacion": "venta",
                    "tipo": "casa",
                    "ciudad": "asuncion",
                    "precio_max": 200000,
                },
            }
        ],
        "model": "claude-haiku-4-5-20251001",
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 150, "output_tokens": 50},
    }

"""Tests for the in-process COUNT cache in contact_service (C1.2).

Strategy: patch `time.monotonic` to control TTL expiry without sleeping,
and patch `contact_repo.count_all` to count DB hits.  No real DB needed.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest

import app.services.contact_service as _svc_module
from app.services.contact_service import (
    ContactService,
    _invalidate_count_cache,
    _COUNT_CACHE,
    _COUNT_TTL_SECS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> AsyncMock:
    return AsyncMock()


def _mock_contact(**kw) -> MagicMock:
    c = MagicMock()
    c.property_id = None
    c.source = "manual"
    c.infocasas_ref = None
    c.baja_at = None
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cache():
    """Clear the module-level cache before and after every test."""
    _COUNT_CACHE.clear()
    yield
    _COUNT_CACHE.clear()


# ---------------------------------------------------------------------------
# C1.2-a: cache HIT — second call does not hit DB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_count_cache_hit_skips_db():
    """After the first call populates the cache, a second call returns the
    cached value without querying count_all again."""
    db = _make_db()
    contacts = [_mock_contact()]

    with (
        patch.object(_svc_module.contact_repo, "get_all", new=AsyncMock(return_value=contacts)),
        patch.object(_svc_module.contact_repo, "count_all", new=AsyncMock(return_value=42)) as mock_count,
        patch.object(_svc_module.property_repo, "get_by_ids", new=AsyncMock(return_value={})),
        patch.object(_svc_module.property_repo, "get_ic_by_refs", new=AsyncMock(return_value={})),
    ):
        # First call — populates cache
        result1 = await ContactService.get_contacts(
            db, status="new", source=None, search=None,
            phone_filter=None, page=1, per_page=25, agent_user_id=None,
        )
        # Second call — should hit cache, NOT call count_all again
        result2 = await ContactService.get_contacts(
            db, status="new", source=None, search=None,
            phone_filter=None, page=1, per_page=25, agent_user_id=None,
        )

    assert result1["total"] == 42
    assert result2["total"] == 42
    assert mock_count.call_count == 1  # DB hit only once


# ---------------------------------------------------------------------------
# C1.2-b: cache EXPIRY — stale entry triggers a fresh DB query
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_count_cache_expires_after_ttl():
    """Once the TTL has elapsed the cache entry is ignored and count_all
    is called again.  We simulate expiry by pre-seeding the cache with an
    already-expired entry (expires_at in the past)."""
    db = _make_db()
    contacts = [_mock_contact()]

    # Pre-seed cache with an already-expired entry (expires_at = 0)
    from app.services.contact_service import _count_cache_key
    key = _count_cache_key(None, None, None, None)
    _COUNT_CACHE[key] = (99, 0.0)  # expires_at=0 → always in the past

    with (
        patch.object(_svc_module.contact_repo, "get_all", new=AsyncMock(return_value=contacts)),
        patch.object(_svc_module.contact_repo, "count_all", new=AsyncMock(return_value=7)) as mock_count,
        patch.object(_svc_module.property_repo, "get_by_ids", new=AsyncMock(return_value={})),
        patch.object(_svc_module.property_repo, "get_ic_by_refs", new=AsyncMock(return_value={})),
    ):
        result = await ContactService.get_contacts(
            db, status=None, source=None, search=None,
            phone_filter=None, page=1, per_page=25,
        )

    # The stale cached value (99) must have been ignored; fresh DB value (7) returned
    assert result["total"] == 7
    assert mock_count.call_count == 1  # DB was hit


# ---------------------------------------------------------------------------
# C1.2-c: search bypasses cache entirely
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_count_cache_bypassed_when_search_present():
    """Free-text search queries must never populate or read the cache."""
    db = _make_db()
    contacts = []

    with (
        patch.object(_svc_module.contact_repo, "get_all", new=AsyncMock(return_value=contacts)),
        patch.object(_svc_module.contact_repo, "count_all", new=AsyncMock(return_value=3)) as mock_count,
        patch.object(_svc_module.property_repo, "get_by_ids", new=AsyncMock(return_value={})),
        patch.object(_svc_module.property_repo, "get_ic_by_refs", new=AsyncMock(return_value={})),
    ):
        await ContactService.get_contacts(
            db, status=None, source=None, search="pedro",
            phone_filter=None, page=1, per_page=25,
        )
        await ContactService.get_contacts(
            db, status=None, source=None, search="pedro",
            phone_filter=None, page=1, per_page=25,
        )

    assert mock_count.call_count == 2  # cache never consulted
    assert len(_COUNT_CACHE) == 0       # nothing stored


# ---------------------------------------------------------------------------
# C1.2-d: agent_user_id is part of the cache key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_count_cache_key_includes_agent():
    """Admin (agent_user_id=None) and agent (agent_user_id=5) must have
    separate cache entries so their totals never bleed into each other."""
    db = _make_db()
    contacts = []

    with (
        patch.object(_svc_module.contact_repo, "get_all", new=AsyncMock(return_value=contacts)),
        patch.object(_svc_module.contact_repo, "count_all", new=AsyncMock(side_effect=[100, 3])) as mock_count,
        patch.object(_svc_module.property_repo, "get_by_ids", new=AsyncMock(return_value={})),
        patch.object(_svc_module.property_repo, "get_ic_by_refs", new=AsyncMock(return_value={})),
    ):
        r_admin = await ContactService.get_contacts(
            db, status=None, source=None, search=None,
            phone_filter=None, page=1, per_page=25, agent_user_id=None,
        )
        r_agent = await ContactService.get_contacts(
            db, status=None, source=None, search=None,
            phone_filter=None, page=1, per_page=25, agent_user_id=5,
        )

    assert r_admin["total"] == 100
    assert r_agent["total"] == 3
    assert mock_count.call_count == 2
    assert len(_COUNT_CACHE) == 2  # two separate entries


# ---------------------------------------------------------------------------
# C1.2-e: invalidation clears cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_count_cache_invalidated_on_create():
    """Creating a contact calls _invalidate_count_cache and clears all entries."""
    db = _make_db()
    contacts = []
    new_contact = _mock_contact(id=99, phone="+595981000001", status="new",
                                 email=None, baja_at=None)

    # Pre-populate cache manually
    from app.services.contact_service import _count_cache_key
    key = _count_cache_key(None, None, None, None)
    import time as t
    _COUNT_CACHE[key] = (55, t.monotonic() + 60)
    assert len(_COUNT_CACHE) == 1

    with (
        patch.object(_svc_module.contact_repo, "get_by_phone", new=AsyncMock(return_value=None)),
        patch.object(_svc_module.contact_repo, "create", new=AsyncMock(return_value=new_contact)),
        patch.object(_svc_module.lead_event_repo, "create", new=AsyncMock()),
        patch.object(_svc_module, "validate_phone", return_value=(True, None)),
        patch.object(db, "commit", new=AsyncMock()),
    ):
        await ContactService.create_contact(
            db, name="Test", phone="+595981000001", email=None,
            status="new", operacion=None, zona=None,
            presupuesto_raw="", dormitorios_raw="",
            user_id=1, user_email="x@test.com", user_role="admin",
        )

    assert len(_COUNT_CACHE) == 0


@pytest.mark.asyncio
async def test_count_cache_invalidated_on_status_change():
    """update_status clears the cache."""
    db = _make_db()
    contact = _mock_contact(id=10, status="new", baja_at=None, phone="+595981000002")

    from app.services.contact_service import _count_cache_key
    import time as t
    key = _count_cache_key(None, None, None, None)
    _COUNT_CACHE[key] = (22, t.monotonic() + 60)

    with (
        patch.object(_svc_module.contact_repo, "get_by_id", new=AsyncMock(return_value=contact)),
        patch.object(_svc_module.contact_repo, "update_status", new=AsyncMock(return_value=contact)),
        patch.object(_svc_module.visit_repo, "has_active_for_contact", new=AsyncMock(return_value=False)),
        patch.object(_svc_module.lead_event_repo, "create", new=AsyncMock()),
        patch.object(db, "commit", new=AsyncMock()),
    ):
        await ContactService.update_status(
            db, contact_id=10, new_status="interested",
            user_id=1, user_email="x@test.com", user_role="admin",
        )

    assert len(_COUNT_CACHE) == 0


@pytest.mark.asyncio
async def test_count_cache_invalidated_on_delete():
    """delete_contact clears the cache."""
    db = _make_db()
    contact = _mock_contact(id=11, status="new", baja_at=None, phone="+595981000003")

    from app.services.contact_service import _count_cache_key
    import time as t
    key = _count_cache_key(None, None, None, None)
    _COUNT_CACHE[key] = (8, t.monotonic() + 60)

    with (
        patch.object(_svc_module.contact_repo, "get_by_id", new=AsyncMock(return_value=contact)),
        patch.object(_svc_module.contact_repo, "update_status", new=AsyncMock(return_value=contact)),
        patch.object(_svc_module.visit_repo, "list_by_contact", new=AsyncMock(return_value=[])),
        patch.object(_svc_module.lead_event_repo, "create", new=AsyncMock()),
        patch.object(db, "commit", new=AsyncMock()),
    ):
        await ContactService.delete_contact(
            db, contact_id=11, user_id=1,
            user_email="x@test.com", user_role="admin",
        )

    assert len(_COUNT_CACHE) == 0

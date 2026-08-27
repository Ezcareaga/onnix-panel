"""
TDD K1 — búsqueda con unaccent en contact_repo._build_filter_clause

RED: creates "Asunción Benítez", searches "asuncion benitez" without
     accent chars → must find the contact after the fix.

Uses onnix_dev (snapshot of prod) which has the unaccent extension
installed (same as all property_repo DB tests).

Cleanup pattern mirrors test_contact_service.py: INSERT via conftest db
fixture, DELETE in teardown psql matching pytest email pattern.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.contact_repo import ContactRepository


_PHONE = "+595999000001"  # unique enough — cleaned up in teardown
_CLEANUP_PHONE = _PHONE


@pytest_asyncio.fixture
async def contact_with_accents(db: AsyncSession):
    """Create 'Asunción Benítez' and yield; delete unconditionally after."""
    contact = await ContactRepository.create(
        db,
        name="Asunción Benítez",
        phone=_PHONE,
        source="manual",
        status="new",
    )
    await db.commit()
    try:
        yield contact
    finally:
        # Hard-delete the test row so it doesn't pollute other tests
        from sqlalchemy import text
        await db.execute(text("DELETE FROM contacts WHERE phone = :phone"), {"phone": _CLEANUP_PHONE})
        await db.commit()


class TestSearchWithUnaccent:
    async def test_search_without_accents_finds_accented_name(
        self, contact_with_accents, db: AsyncSession
    ):
        """'asuncion benitez' must match 'Asunción Benítez' (K1 fix)."""
        results = await ContactRepository.get_all(db, search="asuncion benitez")
        ids = [c.id for c in results]
        assert contact_with_accents.id in ids, (
            "Expected to find 'Asunción Benítez' when searching 'asuncion benitez' — "
            "unaccent() must be applied to both column and search term."
        )

    async def test_search_with_accents_still_finds_contact(
        self, contact_with_accents, db: AsyncSession
    ):
        """Searching the exact accented string must also work."""
        results = await ContactRepository.get_all(db, search="Asunción")
        ids = [c.id for c in results]
        assert contact_with_accents.id in ids

    async def test_search_mixed_case_no_accents_finds_contact(
        self, contact_with_accents, db: AsyncSession
    ):
        """Case-insensitive partial match without accents (e.g. 'ASUNCION')."""
        results = await ContactRepository.get_all(db, search="ASUNCION")
        ids = [c.id for c in results]
        assert contact_with_accents.id in ids

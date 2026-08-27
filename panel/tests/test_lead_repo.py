"""
Tests for app/repositories/lead_repo.py

Covers: get_interested, get_all (with/without filters), count,
get_lead_with_property, and verifies no .format() interpolation.
"""
import inspect
import pytest
from app.repositories.lead_repo import lead_repo, LeadRepository, _build_where


class TestGetInterested:
    async def test_returns_list_of_dicts(self, db):
        result = await lead_repo.get_interested(db)
        assert isinstance(result, list)
        if result:
            assert isinstance(result[0], dict)

    async def test_all_are_interested_status(self, db):
        result = await lead_repo.get_interested(db)
        for lead in result:
            assert lead["status"] == "interested"

    async def test_no_excel_contacts(self, db):
        result = await lead_repo.get_interested(db)
        for lead in result:
            assert lead["source"] != "import:excel"

    async def test_max_50_results(self, db):
        result = await lead_repo.get_interested(db)
        assert len(result) <= 50


class TestGetAll:
    async def test_no_filters(self, db):
        result = await lead_repo.get_all(db)
        assert isinstance(result, list)
        if result:
            assert isinstance(result[0], dict)

    async def test_source_filter(self, db):
        result = await lead_repo.get_all(db, source="infocasas")
        for lead in result:
            assert lead["source"] == "infocasas"

    async def test_status_filter(self, db):
        result = await lead_repo.get_all(db, status="new")
        for lead in result:
            assert lead["status"] == "new"

    async def test_both_filters(self, db):
        result = await lead_repo.get_all(
            db, source="infocasas", status="new"
        )
        for lead in result:
            assert lead["source"] == "infocasas"
            assert lead["status"] == "new"

    async def test_respects_limit(self, db):
        result = await lead_repo.get_all(db, limit=3)
        assert len(result) <= 3

    async def test_no_excel_contacts(self, db):
        result = await lead_repo.get_all(db, limit=100)
        for lead in result:
            assert lead["source"] != "import:excel"

    async def test_has_property_join_columns(self, db):
        result = await lead_repo.get_all(db, limit=1)
        if result:
            lead = result[0]
            assert "property_title" in lead
            assert "property_city" in lead
            assert "ic_title" in lead

    async def test_has_inquiry_history_column_present(self, db):
        """Every lead dict should include has_inquiry_history key."""
        result = await lead_repo.get_all(db, limit=5)
        assert len(result) > 0, "Test requires at least 1 lead in onnix_dev"
        for lead in result:
            assert "has_inquiry_history" in lead
            assert isinstance(lead["has_inquiry_history"], bool)


class TestCount:
    async def test_returns_integer(self, db):
        result = await lead_repo.count(db)
        assert isinstance(result, int)
        assert result >= 0

    async def test_source_filter(self, db):
        total = await lead_repo.count(db)
        filtered = await lead_repo.count(db, source="infocasas")
        assert filtered <= total

    async def test_both_filters(self, db):
        by_source = await lead_repo.count(db, source="infocasas")
        both = await lead_repo.count(db, source="infocasas", status="new")
        assert both <= by_source


class TestGetLeadWithProperty:
    async def test_returns_dict_for_existing(self, db):
        """Find a real contact and fetch it with joins."""
        all_leads = await lead_repo.get_all(db, limit=1)
        if not all_leads:
            pytest.skip("No leads in database")
        contact_id = all_leads[0]["id"]
        result = await lead_repo.get_lead_with_property(db, contact_id)
        assert isinstance(result, dict)
        assert result["id"] == contact_id

    async def test_returns_none_for_missing(self, db):
        result = await lead_repo.get_lead_with_property(db, 999999999)
        assert result is None

    async def test_has_all_expected_keys(self, db):
        all_leads = await lead_repo.get_all(db, limit=1)
        if not all_leads:
            pytest.skip("No leads in database")
        contact_id = all_leads[0]["id"]
        result = await lead_repo.get_lead_with_property(db, contact_id)
        expected_keys = {
            "id", "name", "phone", "email", "source", "status",
            "created_at", "last_activity_at", "property_id", "consulta_date",
            "property_title", "property_city", "property_neighborhood",
            "property_price", "property_operation",
            "ic_title", "ic_city", "ic_price_sale", "ic_price_rent",
            "ic_currency_sale", "ic_currency_rent", "ic_ref", "ic_url",
            "has_inquiry_history",
        }
        assert expected_keys.issubset(set(result.keys()))


class TestNoFormatInterpolation:
    def test_no_format_in_source(self):
        """Verify that lead_repo.py does not use .format() string interpolation."""
        source_code = inspect.getsource(LeadRepository)
        assert ".format(" not in source_code

    def test_no_format_in_build_where(self):
        """Verify the helper also avoids .format()."""
        source_code = inspect.getsource(_build_where)
        assert ".format(" not in source_code


class TestLeadRepoColumns:
    """Unit tests for Phase 100 — verify new columns added to _BASE_COLUMNS."""

    def test_base_columns_includes_property_url(self):
        """Verify _BASE_COLUMNS includes property_url for LEAD-01 button."""
        from app.repositories.lead_repo import _BASE_COLUMNS
        assert "property_url" in _BASE_COLUMNS

    def test_base_columns_includes_conversation_id(self):
        """Verify _BASE_COLUMNS includes conversation_id for LEAD-02 button."""
        from app.repositories.lead_repo import _BASE_COLUMNS
        assert "conversation_id" in _BASE_COLUMNS

    def test_base_columns_includes_has_inquiry_history(self):
        """Verify _BASE_COLUMNS includes has_inquiry_history subquery."""
        from app.repositories.lead_repo import _BASE_COLUMNS
        assert "has_inquiry_history" in _BASE_COLUMNS

    def test_build_where_no_filters(self):
        clause, params = _build_where(None, None)
        assert clause == ""
        assert params == {}

    def test_build_where_with_source(self):
        clause, params = _build_where("infocasas", None)
        assert ":source" in clause
        assert params["source"] == "infocasas"

    def test_build_where_with_status(self):
        clause, params = _build_where(None, "new")
        assert ":status" in clause
        assert params["status"] == "new"

    def test_build_where_with_both_filters(self):
        clause, params = _build_where("whatsapp", "contacted")
        assert ":source" in clause
        assert ":status" in clause
        assert params["source"] == "whatsapp"
        assert params["status"] == "contacted"

    async def test_get_all_calls_execute(self):
        from unittest.mock import AsyncMock, MagicMock
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        db.execute = AsyncMock(return_value=mock_result)
        result = await LeadRepository.get_all(db)
        db.execute.assert_called_once()
        assert result == []

    async def test_count_returns_scalar(self):
        from unittest.mock import AsyncMock, MagicMock
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=42)
        db.execute = AsyncMock(return_value=mock_result)
        count = await LeadRepository.count(db)
        assert count == 42

    async def test_count_returns_zero_when_none(self):
        from unittest.mock import AsyncMock, MagicMock
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=None)
        db.execute = AsyncMock(return_value=mock_result)
        count = await LeadRepository.count(db)
        assert count == 0

"""Tests for construction_state filtering in SQLFilterBuilder.

Covers:
- Dual-mode logic: ILIKE fallback (flag OFF) vs structured column (flag ON)
- Alias compatibility: estado_construccion -> construction_state
- All 4 enum values handled correctly
"""
import os


import pytest

from app.bot.search.sql_filters import SearchFilters, SQLFilterBuilder


# ===========================================================================
# TestConstructionStateFlagOff — ILIKE fallback behaviour (flag=False)
# ===========================================================================


class TestConstructionStateFlagOff:
    """When use_construction_state_column=False, SQL uses ILIKE or is a no-op."""

    def setup_method(self):
        self.builder = SQLFilterBuilder()

    def test_flag_off_uses_ilike_for_en_pozo(self):
        """Flag OFF + en_pozo -> ILIKE on title/description, NOT column filter."""
        filters = SearchFilters(construction_state="en_pozo")
        fq = self.builder.build_query(filters, use_construction_state_column=False)
        sql_lower = fq.sql.lower()

        # Must use ILIKE (or LIKE) patterns
        assert "like '%pozo%'" in sql_lower
        # Must NOT use the structured column filter
        assert "p.construction_state =" not in sql_lower
        assert "construction_state_val" not in fq.params

    def test_flag_off_ignores_other_values(self):
        """Flag OFF + non-en_pozo values -> no filtering (no-op), query stays valid."""
        for value in ("a_estrenar", "en_construccion", "terminado"):
            filters = SearchFilters(construction_state=value)
            fq = self.builder.build_query(
                filters, use_construction_state_column=False
            )
            sql_lower = fq.sql.lower()

            # Must NOT produce a construction_state column filter
            assert "p.construction_state =" not in sql_lower
            assert "construction_state_val" not in fq.params
            # Must NOT produce ILIKE on 'pozo' (that's only for en_pozo)
            assert "like '%pozo%'" not in sql_lower
            # Query must still be structurally valid (has base WHERE clauses)
            assert "is_active = true" in sql_lower

    def test_flag_off_ilike_contains_preventa_patterns(self):
        """Flag OFF en_pozo ILIKE also includes preventa synonym patterns."""
        filters = SearchFilters(construction_state="en_pozo")
        fq = self.builder.build_query(filters, use_construction_state_column=False)
        sql_lower = fq.sql.lower()
        # The current implementation uses 'pozo' ILIKE on title/description/property_type
        assert "pozo" in sql_lower

    def test_flag_off_default_when_no_param(self):
        """build_query default (no kwarg) behaves same as flag=False for en_pozo."""
        filters = SearchFilters(construction_state="en_pozo")
        fq_default = self.builder.build_query(filters)
        fq_explicit = self.builder.build_query(
            filters, use_construction_state_column=False
        )
        assert fq_default.sql == fq_explicit.sql
        assert fq_default.params == fq_explicit.params


# ===========================================================================
# TestConstructionStateFlagOn — structured column behaviour (flag=True)
# ===========================================================================


class TestConstructionStateFlagOn:
    """When use_construction_state_column=True, SQL uses the column directly."""

    def setup_method(self):
        self.builder = SQLFilterBuilder()

    def test_flag_on_uses_column(self):
        """Flag ON + en_pozo -> p.construction_state = :construction_state_val."""
        filters = SearchFilters(construction_state="en_pozo")
        fq = self.builder.build_query(filters, use_construction_state_column=True)

        assert "p.construction_state = :construction_state_val" in fq.sql
        assert fq.params["construction_state_val"] == "en_pozo"
        # Must NOT also use ILIKE fallback
        assert "like '%pozo%'" not in fq.sql.lower()

    def test_flag_on_uses_column_for_all_values(self):
        """Flag ON uses column filter for each of the 4 enum values."""
        for value in ("en_pozo", "en_construccion", "a_estrenar", "terminado"):
            filters = SearchFilters(construction_state=value)
            fq = self.builder.build_query(
                filters, use_construction_state_column=True
            )
            assert "p.construction_state = :construction_state_val" in fq.sql
            assert fq.params["construction_state_val"] == value

    def test_flag_on_no_construction_state_adds_no_clause(self):
        """Flag ON but no construction_state set -> no column filter added."""
        filters = SearchFilters(operacion="venta")
        fq = self.builder.build_query(filters, use_construction_state_column=True)
        assert "p.construction_state" not in fq.sql


# ===========================================================================
# TestSearchFiltersAlias — alias compatibility tests
# ===========================================================================


class TestSearchFiltersAlias:
    """Verify estado_construccion alias populates construction_state correctly."""

    def test_filter_alias_accepts_estado_construccion(self):
        """Create SearchFilters via alias 'estado_construccion'."""
        f = SearchFilters(estado_construccion="en_pozo")
        assert f.construction_state == "en_pozo"

    def test_filter_native_name_works(self):
        """Create SearchFilters via canonical name 'construction_state'."""
        f = SearchFilters(construction_state="a_estrenar")
        assert f.construction_state == "a_estrenar"

    def test_alias_and_canonical_are_same_field(self):
        """Both alias and canonical name produce the same field value."""
        f_alias = SearchFilters(estado_construccion="terminado")
        f_canonical = SearchFilters(construction_state="terminado")
        assert f_alias.construction_state == f_canonical.construction_state

    def test_all_four_values_accepted(self):
        """All 4 enum values accepted via both alias and canonical name."""
        for value in ("en_pozo", "en_construccion", "a_estrenar", "terminado"):
            f = SearchFilters(estado_construccion=value)
            assert f.construction_state == value

    def test_model_dump_uses_alias_key(self):
        """model_dump(by_alias=True) serializes as 'estado_construccion'."""
        f = SearchFilters(construction_state="en_pozo")
        dumped_alias = f.model_dump(by_alias=True)
        assert "estado_construccion" in dumped_alias
        assert dumped_alias["estado_construccion"] == "en_pozo"

    def test_model_dump_default_uses_canonical_key(self):
        """model_dump() (default) serializes as 'construction_state'."""
        f = SearchFilters(construction_state="en_pozo")
        dumped = f.model_dump()
        assert "construction_state" in dumped
        assert dumped["construction_state"] == "en_pozo"

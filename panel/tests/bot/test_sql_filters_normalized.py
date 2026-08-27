"""Tests for property_type_normalized filtering in sql_filters.py.

Covers:
- _resolve_tipo_to_id: all 11 catalog types + aliases + edge cases
- SQLFilterBuilder.build_query: normalized FK path vs ILIKE fallback
- tools.py: tipo field is an explicit enum of 11 values
- orchestrator.py: _PLURAL_MAP covers all catalog types
"""
import os


import pytest

from app.bot.search.sql_filters import (
    SQLFilterBuilder,
    SearchFilters,
    _resolve_tipo_to_id,
)


# ===========================================================================
# TestResolveTipoToId
# ===========================================================================


class TestResolveTipoToId:
    """_resolve_tipo_to_id must map all catalog types correctly."""

    def test_casa_maps_to_1(self):
        assert _resolve_tipo_to_id("casa") == 1

    def test_departamento_maps_to_2(self):
        assert _resolve_tipo_to_id("departamento") == 2

    def test_duplex_maps_to_3(self):
        assert _resolve_tipo_to_id("duplex") == 3

    def test_terreno_maps_to_4(self):
        assert _resolve_tipo_to_id("terreno") == 4

    def test_oficina_maps_to_5(self):
        assert _resolve_tipo_to_id("oficina") == 5

    def test_local_maps_to_6(self):
        assert _resolve_tipo_to_id("local") == 6

    def test_deposito_maps_to_7(self):
        assert _resolve_tipo_to_id("deposito") == 7

    def test_quinta_maps_to_8(self):
        assert _resolve_tipo_to_id("quinta") == 8

    def test_campo_maps_to_9(self):
        assert _resolve_tipo_to_id("campo") == 9

    def test_edificio_maps_to_10(self):
        assert _resolve_tipo_to_id("edificio") == 10

    def test_otro_maps_to_99(self):
        assert _resolve_tipo_to_id("otro") == 99

    def test_ph_alias_maps_to_duplex(self):
        """'ph' is an alias for duplex in Paraguay."""
        assert _resolve_tipo_to_id("ph") == 3

    def test_case_insensitive_upper(self):
        assert _resolve_tipo_to_id("CASA") == 1

    def test_case_insensitive_mixed(self):
        assert _resolve_tipo_to_id("Departamento") == 2

    def test_strips_whitespace(self):
        assert _resolve_tipo_to_id("  casa  ") == 1

    def test_unknown_type_returns_none(self):
        assert _resolve_tipo_to_id("unknown_xyz") is None

    def test_none_returns_none(self):
        assert _resolve_tipo_to_id(None) is None


# ===========================================================================
# TestSQLFiltersNormalized — SQL shape for known/unknown types
# ===========================================================================


class TestSQLFiltersNormalized:
    """build_query must use property_type_normalized for known types with ILIKE fallback."""

    def setup_method(self):
        self.builder = SQLFilterBuilder()

    def _build(self, **kwargs) -> object:
        filters = SearchFilters(**kwargs)
        return self.builder.build_query(filters)

    # --- Known types: must use normalized FK path ---

    def test_tipo_casa_uses_normalized_id(self):
        result = self._build(tipo="casa")
        assert "property_type_normalized" in result.sql
        assert result.params.get("tipo_id") == 1

    def test_tipo_departamento_uses_normalized_id(self):
        result = self._build(tipo="departamento")
        assert "property_type_normalized" in result.sql
        assert result.params.get("tipo_id") == 2

    def test_tipo_duplex_id_3(self):
        result = self._build(tipo="duplex")
        assert result.params.get("tipo_id") == 3

    def test_tipo_ph_maps_to_3(self):
        result = self._build(tipo="ph")
        assert "property_type_normalized" in result.sql
        assert result.params.get("tipo_id") == 3

    def test_tipo_terreno_id_4(self):
        result = self._build(tipo="terreno")
        assert result.params.get("tipo_id") == 4

    def test_tipo_oficina_id_5(self):
        result = self._build(tipo="oficina")
        assert result.params.get("tipo_id") == 5

    def test_tipo_local_id_6(self):
        result = self._build(tipo="local")
        assert result.params.get("tipo_id") == 6

    def test_tipo_deposito_id_7(self):
        result = self._build(tipo="deposito")
        assert result.params.get("tipo_id") == 7

    def test_tipo_quinta_id_8(self):
        result = self._build(tipo="quinta")
        assert result.params.get("tipo_id") == 8

    def test_tipo_campo_id_9(self):
        result = self._build(tipo="campo")
        assert result.params.get("tipo_id") == 9

    def test_tipo_edificio_id_10(self):
        result = self._build(tipo="edificio")
        assert result.params.get("tipo_id") == 10

    def test_tipo_otro_id_99(self):
        result = self._build(tipo="otro")
        assert result.params.get("tipo_id") == 99

    # --- Fallback for NULL rows: ILIKE clause must be present ---

    def test_known_tipo_has_ilike_fallback_for_nulls(self):
        """Known types must include ILIKE fallback for rows where normalized IS NULL."""
        result = self._build(tipo="casa")
        sql_lower = result.sql.lower()
        # SQL must contain IS NULL guard + ILIKE fallback
        assert "property_type_normalized is null" in sql_lower
        assert "f_unaccent" in sql_lower

    def test_known_tipo_ilike_param_set(self):
        """The ILIKE param :tipo must be set for the NULL fallback."""
        result = self._build(tipo="casa")
        assert ":tipo" in result.sql
        assert "casa" in result.params["tipo"]

    # --- Unknown type: pure ILIKE, no FK ---

    def test_unknown_tipo_uses_ilike_fallback_only(self):
        result = self._build(tipo="unknown_xyz")
        sql_lower = result.sql.lower()
        assert "f_unaccent" in sql_lower
        # Must NOT attempt the normalized FK path (no tipo_id param)
        assert "tipo_id" not in result.params

    def test_unknown_tipo_no_normalized_column(self):
        result = self._build(tipo="unknown_xyz")
        # property_type_normalized should not appear since we fell back to ILIKE only
        assert "property_type_normalized" not in result.sql

    # --- Isolation: departamento must not overlap with oficina ---

    def test_departamento_does_not_match_oficina(self):
        """With integer FK filter, departamento (2) cannot match oficina (5)."""
        dep_result = self._build(tipo="departamento")
        ofic_result = self._build(tipo="oficina")
        assert dep_result.params.get("tipo_id") == 2
        assert ofic_result.params.get("tipo_id") == 5
        # Different integer IDs guarantee no cross-match

    # --- No tipo: no normalized filter applied ---

    def test_no_tipo_filter_no_normalized_clause(self):
        result = self._build(operacion="venta")
        assert "property_type_normalized" not in result.sql
        assert "tipo_id" not in result.params


# ===========================================================================
# TestToolEnumDefinition
# ===========================================================================


class TestToolEnumDefinition:
    """search_properties tool must define tipo as an explicit enum of 11 values."""

    def _get_search_tool(self):
        from app.bot.ai.tools import get_tools
        tools = get_tools()
        return next(t for t in tools if t["name"] == "search_properties")

    def test_tipo_field_is_enum(self):
        search_tool = self._get_search_tool()
        tipo_prop = search_tool["input_schema"]["properties"]["tipo"]
        assert "enum" in tipo_prop, "tipo debe ser un enum explícito"

    def test_tipo_enum_has_all_catalog_types(self):
        search_tool = self._get_search_tool()
        tipo_prop = search_tool["input_schema"]["properties"]["tipo"]
        expected = {
            "casa", "departamento", "duplex", "terreno", "oficina",
            "local", "deposito", "quinta", "campo", "edificio", "otro",
        }
        actual = set(tipo_prop["enum"])
        assert expected == actual, f"Missing: {expected - actual}, Extra: {actual - expected}"

    def test_tipo_enum_has_exactly_11_values(self):
        search_tool = self._get_search_tool()
        tipo_prop = search_tool["input_schema"]["properties"]["tipo"]
        assert len(tipo_prop["enum"]) == 11


# ===========================================================================
# TestPluralMapCoverage
# ===========================================================================


class TestPluralMapCoverage:
    """_PLURAL_MAP in orchestrator must cover all 10 main catalog types."""

    def test_plural_map_has_all_catalog_types(self):
        from app.bot.handlers._utils import _PLURAL_MAP
        required = [
            "casa", "departamento", "duplex", "terreno", "oficina",
            "local", "deposito", "quinta", "campo", "edificio",
        ]
        missing = [t for t in required if t not in _PLURAL_MAP]
        assert not missing, f"Missing _PLURAL_MAP entries: {missing}"

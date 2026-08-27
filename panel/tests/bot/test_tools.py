"""Tests for bot AI tool definitions."""
from __future__ import annotations

import pytest

from app.bot.ai.tools import TOOLS, get_tools


class TestTools:
    """Validate the Claude tool-use tool definitions."""

    def test_tools_is_list(self):
        """TOOLS is a list with exactly 5 items (M5.1: added resolver_zona)."""
        assert isinstance(TOOLS, list)
        assert len(TOOLS) == 5

    def test_search_properties_schema(self):
        """search_properties has name, description, and expected properties."""
        tool = next(t for t in TOOLS if t["name"] == "search_properties")
        assert "description" in tool
        schema = tool["input_schema"]
        props = schema["properties"]
        for key in ("operacion", "tipo", "ciudad", "barrio", "precio_max", "dormitorios_min", "dormitorios_max"):
            assert key in props, f"Missing property: {key}"

    def test_search_properties_operacion_enum(self):
        """operacion property has enum [venta, alquiler]."""
        tool = next(t for t in TOOLS if t["name"] == "search_properties")
        operacion = tool["input_schema"]["properties"]["operacion"]
        assert operacion["enum"] == ["venta", "alquiler"]

    def test_get_property_detail_requires_referencia(self):
        """get_property_detail requires 'referencia'."""
        tool = next(t for t in TOOLS if t["name"] == "get_property_detail")
        assert "referencia" in tool["input_schema"]["required"]

    def test_register_lead_exists(self):
        """register_lead tool exists and has an input_schema."""
        tool = next(t for t in TOOLS if t["name"] == "register_lead")
        assert "input_schema" in tool
        assert "properties" in tool["input_schema"]

    def test_get_tools_returns_copy(self):
        """get_tools() returns a fresh list isolated from TOOLS.

        M6.3 Plan 123-02: default get_tools() is the 'busqueda' set = the 5
        originals + agendar_visita (6 tools). The 5 originals must still all be
        present and mutating the returned copy must not affect TOOLS.
        """
        result = get_tools()
        assert result is not TOOLS
        # All 5 originals are present in the default (busqueda) set
        original_names = {t["name"] for t in TOOLS}
        result_names = {t["name"] for t in result}
        assert original_names.issubset(result_names)
        assert "agendar_visita" in result_names
        assert len(result) == 6
        # Mutating the copy must not affect the original
        result.append({"name": "fake"})
        assert len(TOOLS) == 5  # M5.1: 5 tools (added resolver_zona)

    def test_polish04_register_lead_description_and_schema_guard(self):
        """POLISH-04: the register_lead description cues evasive/partial
        derivation; the input schema stays motivo-only with required: []."""
        tools = get_tools("recepcionista")
        tool = next(t for t in tools if t["name"] == "register_lead")

        desc = tool["description"].lower()
        assert "evad" in desc
        assert "parcial" in desc

        # Schema-guard: no accidental param add. motivo-only, required: [].
        schema = tool["input_schema"]
        assert set(schema["properties"].keys()) == {"motivo"}
        assert schema["required"] == []

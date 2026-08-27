"""Tests for get_tools(mode) tool-set filtering by bot mode.

Plan 123-02 (BOT-03/BOT-04): the per-turn mode router drives which tools
Claude sees. recepcionista mode hides search_properties and exposes
agendar_visita; busqueda mode exposes all 6 tools (the 5 originals +
agendar_visita).
"""
from __future__ import annotations

from app.bot.ai.tools import get_tools


_RECEPCIONISTA_EXPECTED = {
    "get_property_detail",
    "register_lead",
    "process_opt_out",
    "resolver_zona",
    "agendar_visita",
}
_BUSQUEDA_EXPECTED = {
    "search_properties",
    "get_property_detail",
    "register_lead",
    "process_opt_out",
    "resolver_zona",
    "agendar_visita",
}


def test_get_tools_recepcionista_excludes_search():
    """recepcionista mode hides search_properties, exposes agendar_visita."""
    names = {t["name"] for t in get_tools("recepcionista")}
    assert "search_properties" not in names
    assert names == _RECEPCIONISTA_EXPECTED


def test_get_tools_busqueda_has_all():
    """busqueda mode returns all 6 tools (5 originals + agendar_visita)."""
    names = {t["name"] for t in get_tools("busqueda")}
    assert names == _BUSQUEDA_EXPECTED
    assert len(get_tools("busqueda")) == 6


def test_get_tools_default_is_busqueda():
    """No-arg call defaults to busqueda (full tool set)."""
    default_names = {t["name"] for t in get_tools()}
    busqueda_names = {t["name"] for t in get_tools("busqueda")}
    assert default_names == busqueda_names

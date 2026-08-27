"""Tests for prompt build_search_context_section with pending_alternatives (Fase F).

Covers:
10. state.pending_alternatives set → prompt contains ALTERNATIVAS block + labels.
11. state.pending_alternatives empty → prompt does NOT contain the block.
"""
from __future__ import annotations

import sys
from pathlib import Path

_panel_dir = str(Path(__file__).resolve().parent.parent)
if _panel_dir not in sys.path:
    sys.path.insert(0, _panel_dir)

from app.bot.ai.prompts import build_search_context_section
from app.bot.core.types import ConversationState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_alt(alt_id: str, label: str, count: int = 5) -> dict:
    return {
        "id": alt_id,
        "label": label,
        "count": count,
        "filters": {"ciudad": alt_id.split(":")[-1]},
        "reason": "zona vecina",
        "callback_payload": f"ALT:{alt_id}",
    }


# ---------------------------------------------------------------------------
# Test 10: alternatives block emitted when pending
# ---------------------------------------------------------------------------

class TestPromptIncludesAlternativesBlock:
    def test_prompt_includes_alternatives_block_when_pending(self):
        """State with pending_alternatives → prompt contains ALTERNATIVAS DISPONIBLES."""
        state = ConversationState()
        state.filtros = {"tipo": "departamento", "ciudad": "villa_morra", "operacion": "venta"}
        state.pending_alternatives = [
            _make_alt("zona_vecina:lambare", "En Lambare hay 8 deptos", 8),
            _make_alt("zona_vecina:luque", "En Luque hay 5 deptos", 5),
        ]

        section = build_search_context_section(state)

        assert "ALTERNATIVAS DISPONIBLES" in section
        assert "En Lambare hay 8 deptos" in section
        assert "En Luque hay 5 deptos" in section
        # Counts should appear
        assert "8 disponibles" in section
        assert "5 disponibles" in section

    def test_prompt_includes_reglas_duras_block(self):
        """When alternatives present, the REGLAS DURAS block is included."""
        state = ConversationState()
        state.filtros = {"tipo": "casa"}
        state.pending_alternatives = [
            _make_alt("zona_vecina:luque", "En Luque hay 3 casas", 3),
        ]

        section = build_search_context_section(state)

        assert "REGLAS DURAS" in section
        assert "NUNCA digas" in section

    def test_prompt_alternatives_numbered_correctly(self):
        """Alternatives appear numbered (1., 2., 3.)."""
        state = ConversationState()
        state.filtros = {"tipo": "terreno"}
        state.pending_alternatives = [
            _make_alt("zona_vecina:lambare", "En Lambare hay 4 terrenos", 4),
            _make_alt("presupuesto_20pct", "Hasta 120k hay 6 terrenos", 6),
        ]

        section = build_search_context_section(state)

        assert "  1." in section
        assert "  2." in section


# ---------------------------------------------------------------------------
# Test 11: alternatives block absent when empty
# ---------------------------------------------------------------------------

class TestPromptOmitsAlternativesBlock:
    def test_prompt_omits_alternatives_block_when_empty(self):
        """State with no pending_alternatives → no ALTERNATIVAS block."""
        state = ConversationState()
        state.filtros = {"tipo": "departamento", "ciudad": "asuncion"}
        state.pending_alternatives = []

        section = build_search_context_section(state)

        assert "ALTERNATIVAS DISPONIBLES" not in section
        assert "REGLAS DURAS" not in section

    def test_prompt_omits_alternatives_block_when_default(self):
        """Default ConversationState (no filtros either) → empty section returned."""
        state = ConversationState()

        section = build_search_context_section(state)

        # Empty state → entire section is blank
        assert "ALTERNATIVAS DISPONIBLES" not in section

    def test_section_nonempty_when_only_filtros_set(self):
        """Filtros set but no alternatives → section has filtros but no alt block."""
        state = ConversationState()
        state.filtros = {"tipo": "casa", "operacion": "alquiler"}

        section = build_search_context_section(state)

        assert section != ""
        assert "ALTERNATIVAS DISPONIBLES" not in section
        assert "tipo: casa" in section

"""Tests for Fase G — few-shots de ambiguedad + regla dura trigger words.

Strategy: Option A — static prompt content checks.
These are deterministic: they validate that the system prompt string contains
the expected instructional fragments added in Fase G. No Claude API calls needed.
"""
from __future__ import annotations

from app.bot.ai.prompts import SYSTEM_PROMPT_TEMPLATE, get_system_prompt


class TestDisambiguationPrompts:
    """Fase G: verifica que los nuevos ejemplos y la regla dura están presentes
    en el prompt generado y sobreviven cambios futuros al template."""

    def test_ambiguous_budget_hasta_triggers_preguntar(self):
        """Ejemplo 4: 'hasta 150K' debe tener instrucción de NO buscar todavía."""
        prompt = get_system_prompt()
        assert "hasta 150K" in prompt
        assert "NO buscar todavía" in prompt or "NO buscar" in prompt

    def test_ambiguous_budget_aprox_triggers_preguntar(self):
        """Ejemplo 4: 'aprox 150K' debe tener instrucción de NO buscar."""
        prompt = get_system_prompt()
        assert "aprox 150K" in prompt
        # The instruction to ask for a max before searching
        assert "tope máximo" in prompt or "tope" in prompt

    def test_unambiguous_range_no_preguntar(self):
        """Ejemplo 6: rango explícito 'entre 100 y 150 m²' debe buscar directo sin preguntar."""
        prompt = get_system_prompt()
        assert "entre 100 y 150 m²" in prompt
        # The instruction says the range is explicit — do NOT ask
        assert "rango es explícito" in prompt or "NO preguntar" in prompt

    def test_trigger_words_listed_in_rule(self):
        """Regla dura: todas las palabras disparadoras deben estar listadas en el prompt."""
        prompt = get_system_prompt()
        # Mandatory trigger words from the hard rule
        assert "máximo" in prompt
        assert "hasta" in prompt
        assert "al menos" in prompt
        assert "aproximadamente" in prompt
        assert "aprox" in prompt

    def test_dormitorios_max_2_still_preguntar(self):
        """Regresión: el ejemplo original (Ejemplo 2 — máximo 2 habitaciones) sigue presente."""
        prompt = get_system_prompt()
        assert "Ejemplo 2" in prompt
        assert "máximo 2 habitaciones" in prompt

    def test_area_al_menos_no_preguntar(self):
        """Ejemplo 5: 'al menos 100m²' debe indicar buscar con area_min=100 sin preguntar."""
        prompt = get_system_prompt()
        assert "al menos 100m²" in prompt or "como mínimo" in prompt
        assert "area_min=100" in prompt
        # The instruction: "como mínimo" is clear — do NOT ask
        assert "NO preguntar" in prompt or "claro" in prompt

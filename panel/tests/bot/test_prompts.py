"""Tests for bot AI prompts and templates."""
from __future__ import annotations

import os
import pytest
from unittest.mock import AsyncMock, patch

from app.bot.ai.prompts import (
    DEFAULT_OPT_OUT_TEXT,
    RESPONSE_TEMPLATES,
    SYSTEM_PROMPT_TEMPLATE,
    _ETAPA_LABELS,
    build_search_context_section,
    get_gemini_system_prompt,
    get_opt_out_text,
    get_response_template,
    get_system_prompt,
    DEFAULT_AI_DUAL_FAIL_TEXT,
    get_ai_dual_fail_text,
)
from app.bot.core.types import ConversationState

GEO_DATA_PATH = os.environ["GEO_DATA_PATH"]

EXPECTED_INTENTS = [
    "saludo",
    "busqueda",
    "busqueda_incompleta",
    "paginacion",
    "detalle",
    "lead",
    "lead_con_nombre",
    "ambiguo_visita",
    "elegir_zona",
    "conversacion",
]


class TestPrompts:
    """Validate system prompt and response templates."""

    def test_system_prompt_has_onnix_branding(self):
        """System prompt mentions Onnix SA."""
        prompt = get_system_prompt()
        assert "Onnix SA" in prompt

    def test_system_prompt_is_spanish(self):
        """System prompt contains common Spanish real-estate terms."""
        prompt = get_system_prompt()
        assert "propiedades" in prompt.lower()
        assert "asistente" in prompt.lower()
        assert "inmobiliaria" in prompt.lower()

    def test_system_prompt_does_not_describe_tools(self):
        """System prompt must NOT describe tools (Claude gets them via API)."""
        prompt = get_system_prompt()
        assert "search_properties" not in prompt
        assert "get_property_detail" not in prompt
        assert "register_lead" not in prompt
        assert "Herramientas disponibles" not in prompt

    def test_gemini_system_prompt_has_limitations(self):
        """Gemini prompt includes no-tools limitation addendum."""
        prompt = get_gemini_system_prompt()
        assert "NO tenes acceso a herramientas" in prompt
        assert "NUNCA describas herramientas" in prompt
        # Should be longer than Claude prompt (has addendum)
        claude_prompt = get_system_prompt()
        assert len(prompt) > len(claude_prompt)

    def test_system_prompt_with_geo_data(self):
        """get_system_prompt now has static top-5 hint regardless of geo_data_path.

        M5.1: geo_data_path is deprecated — geography is a static 5-city hint
        embedded in SYSTEM_PROMPT_TEMPLATE.  The parameter is kept for
        backward compat but ignored.
        """
        prompt = get_system_prompt(GEO_DATA_PATH)
        # Top-5 hint always present (with accents)
        assert "Asunción" in prompt
        assert "Luque" in prompt
        assert "resolver_zona" in prompt
        # Both calls return identical prompt (geo_data_path is ignored)
        base_prompt = get_system_prompt()
        assert prompt == base_prompt

    def test_system_prompt_without_geo_data(self):
        """get_system_prompt without path returns a valid prompt."""
        prompt = get_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 100
        assert "Onnix SA" in prompt

    def test_response_templates_cover_all_intents(self):
        """RESPONSE_TEMPLATES has all expected keys (opt_out moved to prompts.py constant).

        Post M2.F7: added "lead_con_nombre" variant → 10 keys total.
        """
        for intent in EXPECTED_INTENTS:
            assert intent in RESPONSE_TEMPLATES, f"Missing intent: {intent}"
        assert len(RESPONSE_TEMPLATES) == 10

    def test_system_prompt_has_tone_rule(self):
        """System prompt includes professional tone constraint against repetitive expressions."""
        prompt = get_system_prompt()
        assert "NUNCA uses expresiones" in prompt
        assert "dale" in prompt
        assert "profesional y amigable" in prompt.lower()

    def test_system_prompt_no_laughs_rule(self):
        """System prompt prohibits laughs (jaja, jeje) for professional tone."""
        prompt = get_system_prompt()
        assert "NUNCA uses risas" in prompt
        assert "jaja" in prompt
        assert "jeje" in prompt

    def test_gemini_prompt_has_tone_rule(self):
        """Gemini prompt inherits tone constraint from base."""
        prompt = get_gemini_system_prompt()
        assert "NUNCA uses expresiones" in prompt
        assert "NUNCA uses risas" in prompt

    def test_get_response_template_known_intent(self):
        """get_response_template returns non-empty string for known intent."""
        result = get_response_template("saludo")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_response_template_unknown_intent(self):
        """get_response_template returns conversacion template for unknown intent."""
        result = get_response_template("xyz_unknown")
        expected = RESPONSE_TEMPLATES["conversacion"]
        assert result == expected

    def test_busqueda_incompleta_asks_one_question(self):
        """M2.F4: busqueda_incompleta fallback must ask ONE question only.

        Filosofía del bot: un dato por turno. El fallback debe respetar
        la misma regla en vez de preguntar 3 cosas juntas.
        """
        text = get_response_template("busqueda_incompleta")
        qmarks = text.count("?")
        assert qmarks == 1, (
            f"Expected exactly 1 question mark, got {qmarks}. "
            f"Text: {text!r}"
        )
        low = text.lower()
        assert "comprar" in low, "Expected 'comprar' in text"
        assert "alquilar" in low, "Expected 'alquilar' in text"

    def test_system_prompt_forbids_geography_from_memory(self):
        """Bug 2026-04-25: bot afirmó 'Shopping del Sol está en Lambaré' de memoria.

        Claude debe llamar resolver_zona cuando el cliente pregunta sobre
        ubicación de landmarks/barrios, NUNCA responder de memoria.
        """
        prompt = get_system_prompt()
        p_low = prompt.lower()
        # Anti-memory directive: must contain "NUNCA respondas" AND a geography-from-memory phrase
        assert "nunca respondas" in p_low, (
            "System prompt must contain 'NUNCA respondas' directive about geography"
        )
        has_memory_phrase = (
            "de memoria" in p_low
            or "ubicacion" in p_low
            or "ubicación" in p_low
        )
        assert has_memory_phrase, (
            "Anti-memory directive must mention 'memoria' or 'ubicacion/ubicación'"
        )
        # Tool referenced (already enforced elsewhere, kept for explicitness)
        assert "resolver_zona" in prompt

    def test_system_prompt_has_landmark_few_shot(self):
        """System prompt debe incluir un ejemplo few-shot con Shopping del Sol.

        El ejemplo debe mostrar la acción correcta (llamar resolver_zona) frente
        a la pregunta canónica del bug 2026-04-25.
        """
        prompt = get_system_prompt()
        assert "Shopping del Sol" in prompt, (
            "Few-shot example must mention 'Shopping del Sol' (canonical bug case)"
        )
        # Linked to the tool within proximity (~300 chars window after the mention)
        idx = prompt.index("Shopping del Sol")
        window = prompt[idx : idx + 300]
        assert "resolver_zona" in window, (
            "Few-shot example must link 'Shopping del Sol' to resolver_zona within 300 chars"
        )

    def test_system_prompt_instructs_presenting_barrios_cercanos(self):
        """Bug 2026-04-26: bot llamó resolver_zona pero respondió corto sin listar
        los barrios cercanos. El prompt instruye llamar la tool, pero no especifica
        que el campo `barrios_cercanos` debe listarse al cliente.

        El prompt debe nombrar el campo `barrios_cercanos` y dar una pauta clara
        de presentación (listarlos / enumerarlos) cuando la pregunta es geográfica.
        """
        prompt = get_system_prompt()
        # Field name must appear so Claude sabe qué campo usar del tool result
        assert "barrios_cercanos" in prompt, (
            "Prompt must reference the 'barrios_cercanos' field of resolver_zona "
            "so Claude knows which field to surface to the client"
        )
        # Presentation guidance: must instruct enumerating/listing the result
        p_low = prompt.lower()
        has_listing_directive = (
            "listá" in p_low
            or "lista" in p_low
            or "enumera" in p_low
            or "enumerá" in p_low
        )
        assert has_listing_directive, (
            "Prompt must instruct listing/enumerating barrios_cercanos "
            "(words: listá / lista / enumera / enumerá)"
        )

    def test_system_prompt_forbids_filter_inference_from_bot_history(self):
        """Bug 2026-04-25: Claude inyectó ciudad='Lambare' en un tool call
        porque una respuesta previa del bot había dicho 'Shopping del Sol
        está en Lambaré'.

        El prompt debe prohibir explícitamente inferir filtros de
        respuestas previas del bot — los filtros vienen SOLO de mensajes
        explícitos del usuario.
        """
        prompt = get_system_prompt()
        p_low = prompt.lower()
        # Anti-inference rule (accent-flexible)
        assert ("nunca inferí" in p_low) or ("nunca inferi" in p_low), (
            "Prompt must contain 'NUNCA inferí' rule about tool-call filters"
        )
        # Scope: bot's prior responses (NOT user's prior turns)
        bot_scope_phrases = [
            "respuestas previas del bot",
            "respuestas anteriores del bot",
            "respuesta previa del bot",
            "respuestas tuyas previas",
            "respuestas tuyas anteriores",
            "tus respuestas previas",
            "tus respuestas anteriores",
        ]
        assert any(s in p_low for s in bot_scope_phrases), (
            "Anti-inference rule must scope to BOT's prior responses, not user's"
        )

    def test_system_prompt_has_filter_isolation_example(self):
        """Few-shot example showing bot's prior response NOT contaminating
        tool call filters."""
        prompt = get_system_prompt()
        p_low = prompt.lower()
        # Must contain a specific marker phrase used in the FIX 2 example
        has_example_marker = (
            "respuesta tuya previa" in p_low
            or "respuesta tuya anterior" in p_low
            or "tu respuesta previa" in p_low
            or "tu respuesta anterior" in p_low
        )
        assert has_example_marker, (
            "Ejemplo debe usar marcador 'respuesta tuya previa/anterior' o 'tu respuesta previa/anterior'"
        )
        # And show tool-call filter format (e.g., ciudad="Lambaré")
        assert ('ciudad="' in prompt) or ("ciudad='" in prompt) or ("ciudad=" in prompt.replace(" ", "")), (
            "Ejemplo debe mostrar formato de tool call con ciudad=..."
        )

    def test_system_prompt_requires_announcing_relaxed_filters(self):
        """Bug 2026-04-25: bot presentó resultados relajados como si cumplieran
        los filtros originales. El prompt debe exigir que cuando el tool result
        traiga `relaxed_filters` no vacío, Claude lo informe ANTES de mostrar
        propiedades.
        """
        prompt = get_system_prompt()
        p_low = prompt.lower()
        # Must reference the field name from the tool result
        assert "relaxed_filters" in prompt, (
            "Prompt must reference the 'relaxed_filters' field by name"
        )
        # Must contain an explicit obligation
        has_obligation = (
            "debés" in p_low or "debes" in p_low or "obligatorio" in p_low
        )
        assert has_obligation, (
            "Prompt must contain an obligation directive (debés/debes/obligatorio)"
        )
        # Must mention informing the user before showing properties
        assert ("antes de mostrar" in p_low) or ("informá" in p_low) or ("informa" in p_low), (
            "Prompt must direct Claude to inform user (antes de mostrar/informá)"
        )

    def test_system_prompt_has_relaxation_announcement_example(self):
        """System prompt incluye ejemplo de anuncio correcto de relajación."""
        prompt = get_system_prompt()
        p_low = prompt.lower()
        # Must contain phrasing matching the canonical case from the user spec
        # ("No encontré X. Lo más cercano que encontré tiene Y")
        has_no_encontre = "no encontré" in p_low or "no encontre" in p_low
        has_lo_mas_cercano = "lo más cercano" in p_low or "lo mas cercano" in p_low
        assert has_no_encontre and has_lo_mas_cercano, (
            "Ejemplo debe contener fraseo 'No encontré ... Lo más cercano que encontré ...'"
        )


class TestPromptSecurity:
    """System prompt contains anti-injection security rules."""

    def test_never_reveal_system_prompt(self):
        """Prompt instructs to never reveal system prompt."""
        prompt = get_system_prompt()
        assert "system prompt" in prompt.lower()
        assert "NUNCA" in prompt

    def test_never_change_role(self):
        """Prompt instructs to always stay as Onnix assistant."""
        prompt = get_system_prompt()
        assert "Onnix SA" in prompt
        assert "rol" in prompt.lower()

    def test_safe_deflection_response(self):
        """Prompt includes a safe deflection for manipulation attempts."""
        prompt = get_system_prompt()
        assert "propiedades" in prompt.lower()
        # Should have some form of "only help with real estate"
        assert "inmobiliarios" in prompt.lower() or "propiedades" in prompt.lower()

    def test_never_expose_internal_ids(self):
        """Prompt instructs to not show internal IDs."""
        prompt = get_system_prompt()
        assert "IDs" in prompt or "ids" in prompt.lower()

    def test_security_rules_in_gemini_prompt_too(self):
        """Gemini prompt also includes security rules (inherits from base)."""
        prompt = get_gemini_system_prompt()
        assert "system prompt" in prompt.lower()
        assert "NUNCA" in prompt


class TestSearchContextSection:
    """Tests for build_search_context_section (FIX A + BUG 2 fix)."""

    def test_returns_empty_when_no_pending_and_no_filtros(self):
        """No pending results AND no filters → empty string (no prompt bloat)."""
        state = ConversationState(
            filtros={},
            shown_properties=list(range(2)),
            resultados_pendientes=[],
        )
        result = build_search_context_section(state)
        assert result == ""

    def test_filters_visible_when_pending_zero(self):
        """Filters present but pending=0 → section still emitted (BUG 2 fix)."""
        state = ConversationState(
            filtros={"operacion": "venta", "ciudad": "asuncion"},
            shown_properties=list(range(4)),
            resultados_pendientes=[],
        )
        result = build_search_context_section(state)
        assert "venta" in result
        assert "asuncion" in result
        assert "Filtros activos" in result
        assert "Ya mostrados: 4" in result
        # No pagination instruction when pending is 0
        assert "NO ejecutes search_properties" not in result
        assert "Pendientes de mostrar" not in result

    def test_includes_filter_summary(self):
        """Pending results + filtros → readable context section."""
        state = ConversationState(
            filtros={
                "operacion": "alquiler",
                "tipo": "departamento",
                "ciudad": "asuncion",
                "precio_max": 500,
            },
            shown_properties=list(range(2)),
            resultados_pendientes=list(range(12)),
        )
        result = build_search_context_section(state)
        assert "alquiler" in result
        assert "departamento" in result
        assert "asuncion" in result
        assert "500" in result
        assert "Ya mostrados: 2" in result
        assert "Pendientes de mostrar: 12" in result

    def test_includes_no_search_instruction(self):
        """Section tells Claude NOT to call search_properties when pending > 0."""
        state = ConversationState(
            filtros={},
            shown_properties=list(range(2)),
            resultados_pendientes=list(range(5)),
        )
        result = build_search_context_section(state)
        assert "NO ejecutes search_properties" in result

    def test_no_search_instruction_when_pending_zero(self):
        """Pagination instruction absent when pending is 0."""
        state = ConversationState(
            filtros={"operacion": "venta"},
            shown_properties=list(range(2)),
            resultados_pendientes=[],
        )
        result = build_search_context_section(state)
        assert "NO ejecutes search_properties" not in result

    def test_handles_empty_filtros(self):
        """Empty filtros dict → counts only, no crash."""
        state = ConversationState(
            filtros={},
            shown_properties=[],
            resultados_pendientes=list(range(3)),
        )
        result = build_search_context_section(state)
        assert "Pendientes de mostrar: 3" in result
        assert "Filtros activos" not in result

    def test_includes_dormitorios_filter(self):
        """Dormitorios filter is displayed when present (exact match via min=max)."""
        state = ConversationState(
            filtros={"operacion": "venta", "dormitorios_min": 3, "dormitorios_max": 3},
            shown_properties=[],
            resultados_pendientes=list(range(5)),
        )
        result = build_search_context_section(state)
        assert "dormitorios: 3" in result

    def test_accumulation_instruction_when_filters_present(self):
        """When filters exist, section instructs Claude to maintain them."""
        state = ConversationState(
            filtros={"operacion": "alquiler", "ciudad": "luque"},
            shown_properties=list(range(2)),
            resultados_pendientes=[],
        )
        result = build_search_context_section(state)
        assert "MANTENÉ los filtros existentes" in result

    # --- Task 84-01: etapa display ---

    def test_etapa_shown_when_mostrando_resultados(self):
        """Non-inicio etapa emits 'Estado:' line in prompt."""
        state = ConversationState(
            etapa="mostrando_resultados",
            filtros={"operacion": "venta"},
            shown_properties=list(range(2)),
            resultados_pendientes=list(range(5)),
        )
        result = build_search_context_section(state)
        assert "Estado: Mostrando resultados de busqueda" in result

    def test_etapa_hidden_for_inicio(self):
        """etapa='inicio' does NOT produce a 'Estado:' line."""
        state = ConversationState(
            etapa="inicio",
            filtros={"operacion": "venta"},
            shown_properties=[],
            resultados_pendientes=list(range(3)),
        )
        result = build_search_context_section(state)
        assert "Estado:" not in result

    def test_etapa_buscando_label(self):
        """etapa='buscando' shows 'Recopilando filtros'."""
        state = ConversationState(
            etapa="buscando",
            filtros={"operacion": "alquiler"},
            shown_properties=[],
            resultados_pendientes=[],
        )
        result = build_search_context_section(state)
        assert "Estado: Recopilando filtros" in result

    def test_etapa_unknown_no_estado_line(self):
        """Unknown etapa (not in labels) does NOT produce 'Estado:' line."""
        state = ConversationState(
            etapa="some_future_etapa",
            filtros={"operacion": "venta"},
            shown_properties=[],
            resultados_pendientes=list(range(2)),
        )
        result = build_search_context_section(state)
        assert "Estado:" not in result

    def test_etapa_labels_dict_covers_expected_states(self):
        """_ETAPA_LABELS has all documented conversation phases."""
        expected_keys = {
            "inicio", "buscando", "mostrando_resultados",
            "detalle", "viendo_detalle", "contactando_asesor",
        }
        assert set(_ETAPA_LABELS.keys()) == expected_keys

    # --- Task 84-01: precio_min + descripcion_libre ---

    def test_precio_min_and_max_range(self):
        """Both precio_min and precio_max shows range format."""
        state = ConversationState(
            filtros={
                "operacion": "venta",
                "precio_min": 100000,
                "precio_max": 200000,
                "moneda": "usd",
            },
            shown_properties=[],
            resultados_pendientes=list(range(5)),
        )
        result = build_search_context_section(state)
        assert "precio: 100000-200000 USD" in result

    def test_precio_min_only(self):
        """Only precio_min shows 'desde X' format."""
        state = ConversationState(
            filtros={
                "operacion": "alquiler",
                "precio_min": 500,
            },
            shown_properties=[],
            resultados_pendientes=list(range(3)),
        )
        result = build_search_context_section(state)
        assert "desde 500 USD" in result
        assert "hasta" not in result.lower().split("desde")[0]  # no "hasta" before "desde"

    def test_filtros_rule_mentions_tipo_inheritance(self):
        """Filtros block must tell Claude to never drop tipo on refinement."""
        state = ConversationState(
            filtros={"operacion": "venta", "tipo": "casa", "ciudad": "Lambare"},
            shown_properties=list(range(2)),
            resultados_pendientes=list(range(5)),
        )
        result = build_search_context_section(state)
        lowered = result.lower()
        assert "nunca omitas" in lowered
        assert "tipo" in lowered

    def test_new_search_drops_all_non_tipo_non_zone_filters(self):
        """Prompt rule extends 'NO heredes' to dormitorios/baños/área/descripción."""
        state = ConversationState(
            filtros={
                "operacion": "venta",
                "tipo": "casa",
                "ciudad": "Fernando de la Mora",
                "dormitorios_max": 2,
                "precio_max": 200000000,
            },
            shown_properties=list(range(2)),
            resultados_pendientes=list(range(5)),
        )
        result = build_search_context_section(state).lower()
        assert "dormitorios" in result
        assert "baños" in result or "banos" in result
        assert ("descripción" in result or "descripcion" in result)
        assert "superficie" in result or "área" in result or "area" in result


class TestBusquedasHistoricas:
    """busquedas_historicas section — only emitted when filtros is empty."""

    def _entry(self, operacion="alquiler", tipo="departamento", ciudad="Asuncion",
               presupuesto_max=1000, moneda="usd", resultados=5):
        return {
            "fecha": "2026-03-30T22:42:00+00:00",
            "operacion": operacion,
            "tipo": tipo,
            "ciudad": ciudad,
            "barrio": "",
            "presupuesto_max": presupuesto_max,
            "moneda": moneda,
            "resultados_encontrados": resultados,
        }

    def test_emitted_when_filtros_empty_and_history_exists(self):
        """Shows BÚSQUEDAS ANTERIORES when filtros={} and history is non-empty."""
        state = ConversationState(
            filtros={},
            busquedas_historicas=[self._entry()],
        )
        result = build_search_context_section(state)
        assert "BÚSQUEDAS ANTERIORES" in result
        assert "alquiler" in result
        assert "Asuncion" in result
        assert "1000 USD" in result
        assert "5 resultados" in result

    def test_not_emitted_when_filtros_active(self):
        """Does NOT show BÚSQUEDAS ANTERIORES when filtros has data (active search)."""
        state = ConversationState(
            filtros={"operacion": "venta", "ciudad": "Luque"},
            busquedas_historicas=[self._entry()],
            resultados_pendientes=[1, 2, 3],
        )
        result = build_search_context_section(state)
        assert "BÚSQUEDAS ANTERIORES" not in result

    def test_not_emitted_when_history_empty(self):
        """Does NOT show BÚSQUEDAS ANTERIORES when busquedas_historicas is empty."""
        state = ConversationState(filtros={}, busquedas_historicas=[])
        result = build_search_context_section(state)
        assert "BÚSQUEDAS ANTERIORES" not in result
        assert result == ""  # no context at all

    def test_caps_at_last_two_entries(self):
        """Emits at most the 2 most recent entries."""
        entries = [
            self._entry(ciudad="Luque", resultados=2),
            self._entry(ciudad="Fernando de la Mora", resultados=3),
            self._entry(ciudad="Asuncion", resultados=5),
        ]
        state = ConversationState(filtros={}, busquedas_historicas=entries)
        result = build_search_context_section(state)
        assert "Asuncion" in result
        assert "Fernando de la Mora" in result
        assert "Luque" not in result  # oldest entry not shown

    def test_entry_without_optional_fields(self):
        """Handles entry with only some fields filled."""
        entry = {"operacion": "venta", "tipo": "", "ciudad": "", "resultados_encontrados": 0}
        state = ConversationState(filtros={}, busquedas_historicas=[entry])
        result = build_search_context_section(state)
        assert "BÚSQUEDAS ANTERIORES" in result
        assert "venta" in result

    def test_precio_max_only_unchanged(self):
        """Only precio_max still shows 'hasta X' format (backward compat)."""
        state = ConversationState(
            filtros={
                "operacion": "venta",
                "precio_max": 300000,
            },
            shown_properties=[],
            resultados_pendientes=list(range(4)),
        )
        result = build_search_context_section(state)
        assert "hasta 300000 USD" in result

    def test_precio_range_gs_moneda(self):
        """Guaranies moneda is displayed correctly in range."""
        state = ConversationState(
            filtros={
                "precio_min": 200000000,
                "precio_max": 500000000,
                "moneda": "gs",
            },
            shown_properties=[],
            resultados_pendientes=list(range(2)),
        )
        result = build_search_context_section(state)
        assert "precio: 200000000-500000000 GS" in result

    def test_descripcion_libre_shown(self):
        """descripcion_libre in filtros shows in prompt output."""
        state = ConversationState(
            filtros={
                "operacion": "venta",
                "descripcion_libre": "con piscina",
            },
            shown_properties=[],
            resultados_pendientes=list(range(3)),
        )
        result = build_search_context_section(state)
        assert "descripcion: con piscina" in result

    def test_descripcion_libre_empty_not_shown(self):
        """Empty descripcion_libre does NOT appear in output."""
        state = ConversationState(
            filtros={
                "operacion": "venta",
                "descripcion_libre": "",
            },
            shown_properties=[],
            resultados_pendientes=list(range(3)),
        )
        result = build_search_context_section(state)
        assert "descripcion:" not in result

    # --- Task 84-01: last_detalle_id + current_page_ids ---

    def test_last_detalle_id_shown_in_detalle(self):
        """last_detalle_id with etapa='detalle' shows property ID line."""
        state = ConversationState(
            etapa="detalle",
            filtros={},
            shown_properties=[],
            resultados_pendientes=[],
            last_detalle_id=123,
        )
        result = build_search_context_section(state)
        assert "El usuario esta viendo la propiedad ID 123" in result

    def test_last_detalle_id_shown_in_viendo_detalle(self):
        """last_detalle_id with etapa='viendo_detalle' also shows property ID."""
        state = ConversationState(
            etapa="viendo_detalle",
            filtros={},
            shown_properties=[],
            resultados_pendientes=[],
            last_detalle_id=456,
        )
        result = build_search_context_section(state)
        assert "El usuario esta viendo la propiedad ID 456" in result

    def test_last_detalle_id_hidden_when_not_detalle_etapa(self):
        """last_detalle_id present but etapa is NOT detalle — line suppressed."""
        state = ConversationState(
            etapa="mostrando_resultados",
            filtros={"operacion": "venta"},
            shown_properties=list(range(4)),
            resultados_pendientes=[],
            last_detalle_id=123,
        )
        result = build_search_context_section(state)
        assert "El usuario esta viendo la propiedad ID" not in result

    def test_last_detalle_id_none_hidden(self):
        """last_detalle_id=None even with detalle etapa — line suppressed."""
        state = ConversationState(
            etapa="detalle",
            filtros={"operacion": "venta"},
            shown_properties=[],
            resultados_pendientes=[],
            last_detalle_id=None,
        )
        result = build_search_context_section(state)
        assert "El usuario esta viendo la propiedad ID" not in result

    def test_current_page_ids_shown(self):
        """Non-empty current_page_ids emits 'Propiedades en pantalla' line."""
        state = ConversationState(
            filtros={"operacion": "venta"},
            shown_properties=list(range(4)),
            resultados_pendientes=list(range(6)),
            current_page_ids=[100, 101],
        )
        result = build_search_context_section(state)
        assert "Propiedades en pantalla: [100, 101]" in result

    def test_current_page_ids_empty_hidden(self):
        """Empty current_page_ids does NOT emit the line."""
        state = ConversationState(
            filtros={"operacion": "venta"},
            shown_properties=list(range(4)),
            resultados_pendientes=[],
            current_page_ids=[],
        )
        result = build_search_context_section(state)
        assert "Propiedades en pantalla" not in result

    def test_current_page_ids_alone_emits_section(self):
        """current_page_ids alone (no filtros, no pending) still emits section."""
        state = ConversationState(
            filtros={},
            shown_properties=[],
            resultados_pendientes=[],
            current_page_ids=[200, 201, 202],
        )
        result = build_search_context_section(state)
        assert result != ""
        assert "Propiedades en pantalla: [200, 201, 202]" in result

    # --- Task 85-01: total_found, lead_registrado display ---

    def test_total_found_shown_when_positive(self):
        """total_found > 0 emits 'Ultima busqueda encontro: N propiedades'."""
        state = ConversationState(
            filtros={"operacion": "venta"},
            shown_properties=list(range(2)),
            resultados_pendientes=list(range(5)),
            total_found=47,
        )
        result = build_search_context_section(state)
        assert "Ultima busqueda encontro: 47 propiedades" in result

    def test_total_found_hidden_when_zero(self):
        """total_found == 0 does NOT emit the line."""
        state = ConversationState(
            filtros={"operacion": "venta"},
            shown_properties=list(range(2)),
            resultados_pendientes=list(range(3)),
            total_found=0,
        )
        result = build_search_context_section(state)
        assert "Ultima busqueda encontro" not in result

    def test_lead_registrado_shown_when_true(self):
        """lead_registrado=True emits 'El usuario YA solicito contacto'."""
        state = ConversationState(
            filtros={"operacion": "venta"},
            shown_properties=list(range(2)),
            resultados_pendientes=[],
            lead_registrado=True,
        )
        result = build_search_context_section(state)
        assert "El usuario YA solicito contacto con un asesor" in result

    def test_lead_registrado_hidden_when_false(self):
        """lead_registrado=False does NOT emit the line."""
        state = ConversationState(
            filtros={"operacion": "venta"},
            shown_properties=list(range(2)),
            resultados_pendientes=list(range(3)),
            lead_registrado=False,
        )
        result = build_search_context_section(state)
        assert "El usuario YA solicito contacto" not in result

    def test_total_found_and_lead_registrado_together(self):
        """Both total_found and lead_registrado shown in same section."""
        state = ConversationState(
            etapa="mostrando_resultados",
            filtros={"operacion": "venta", "ciudad": "asuncion"},
            shown_properties=list(range(4)),
            resultados_pendientes=list(range(8)),
            total_found=12,
            lead_registrado=True,
        )
        result = build_search_context_section(state)
        assert "Ultima busqueda encontro: 12 propiedades" in result
        assert "El usuario YA solicito contacto con un asesor" in result

    def test_full_state_all_fields(self):
        """Integration test: all new fields present at once."""
        state = ConversationState(
            etapa="mostrando_resultados",
            filtros={
                "operacion": "venta",
                "tipo": "casa",
                "ciudad": "asuncion",
                "precio_min": 80000,
                "precio_max": 150000,
                "dormitorios_min": 3,
                "dormitorios_max": 3,
                "descripcion_libre": "con piscina",
            },
            shown_properties=list(range(4)),
            resultados_pendientes=list(range(10)),
            current_page_ids=[50, 51],
            last_detalle_id=99,  # should NOT show — etapa is not detalle
        )
        result = build_search_context_section(state)
        assert "Estado: Mostrando resultados de busqueda" in result
        assert "precio: 80000-150000 USD" in result
        assert "dormitorios: 3" in result
        assert "descripcion: con piscina" in result
        assert "Ya mostrados: 4" in result
        assert "Pendientes de mostrar: 10" in result
        assert "Propiedades en pantalla: [50, 51]" in result
        # last_detalle_id should NOT show because etapa is not detalle
        assert "El usuario esta viendo la propiedad ID" not in result


# ===========================================================================
# TestBusquedaIncompletaPromptRule  (Bug 6)
# ===========================================================================

class TestBusquedaIncompletaPromptRule:
    """Bug 6 (Fix B): system prompt must instruct Claude to ask one field
    at a time during busqueda_incompleta — never multiple fields at once."""

    def test_busqueda_incompleta_rule_asks_single_field_per_turn(self):
        """System prompt busqueda_incompleta rule must forbid multi-field questions."""
        prompt = get_system_prompt()
        # The rule must explicitly restrict to one question per message
        lower = prompt.lower()
        assert (
            "un solo campo" in lower
            or "una sola pregunta" in lower
            or "por turno" in lower
            or "nunca más de una pregunta" in lower
            or "nunca hagas más de una pregunta" in lower
        ), (
            "busqueda_incompleta rule must tell Claude to ask one field at a time"
        )


# ===========================================================================
# GRUPO 3: Refactoring tests
# ===========================================================================


class TestGrupo3ToneRefinement:
    """Cambio 3.1: Tone refinement — new forbidden words and variation instruction."""

    def test_tone_rule_includes_super_increible(self):
        """Tone rule must include 'super' and 'increible' as forbidden expressions."""
        prompt = get_system_prompt()
        lower = prompt.lower()
        assert "super" in lower, "Tone rule must forbid 'super'"
        assert "increible" in lower, "Tone rule must forbid 'increible'"

    def test_tone_rule_asks_for_variation(self):
        """Tone rule must ask Claude to vary its responses (not be repetitive)."""
        prompt = get_system_prompt()
        lower = prompt.lower()
        assert "varia" in lower or "vari" in lower, (
            "Tone rule must instruct Claude to vary responses"
        )

    def test_tone_rule_describes_warmth_and_directness(self):
        """Tone rule should describe desired tone as warm and direct."""
        prompt = get_system_prompt()
        lower = prompt.lower()
        assert "calido" in lower or "cálido" in lower or "directo" in lower, (
            "Tone rule must mention 'calido' or 'directo'"
        )


class TestGrupo3BusquedaIncompletaRefinement:
    """Cambio 3.2: busqueda_incompleta must show priority and partial-filter example."""

    def test_busqueda_incompleta_shows_priority_order(self):
        """busqueda_incompleta rule must specify priority order: operacion > zona > presupuesto."""
        prompt = get_system_prompt()
        assert "operacion" in prompt.lower() and "zona" in prompt.lower(), (
            "busqueda_incompleta must mention operacion and zona priority"
        )
        # The priority notation should appear somewhere in the prompt
        assert "operacion > zona" in prompt or "operacion > zona > presupuesto" in prompt, (
            "busqueda_incompleta must show operacion > zona priority"
        )

    def test_busqueda_incompleta_has_acknowledge_example(self):
        """busqueda_incompleta must show example of acknowledging partial filter."""
        prompt = get_system_prompt()
        # The example pattern: "Perfecto, [tipo] en [operacion]. En que zona buscas?"
        assert "acusalo" in prompt or "En que zona" in prompt or "acus" in prompt, (
            "busqueda_incompleta must include acknowledge example"
        )


class TestGrupo3NoResultsGeneral:
    """Cambio 3.3: Sin resultados generales instruction."""

    def test_no_results_general_instruction_exists(self):
        """Prompt must include instruction for 0 results without min_price_in_zone."""
        prompt = get_system_prompt()
        assert "Sin resultados generales" in prompt or (
            "zonas cercanas" in prompt or "zonas cercanas" in prompt.lower()
        ), "Prompt must include 'Sin resultados generales' section"

    def test_no_results_never_say_no_hay_nada(self):
        """Prompt must forbid 'no hay nada' phrasing."""
        prompt = get_system_prompt()
        assert "NUNCA digas" in prompt or "nunca digas" in prompt.lower(), (
            "Prompt must instruct Claude to never say 'no hay nada'"
        )

    def test_no_results_offers_alternative(self):
        """No-results instruction must offer an alternative (nearby zones or budget adjustment)."""
        prompt = get_system_prompt()
        lower = prompt.lower()
        assert "alternativa" in lower or "zonas cercanas" in lower or "ajustar" in lower, (
            "No-results instruction must offer an alternative"
        )


class TestGrupo3ResponseTemplates:
    """Cambio 3.4: RESPONSE_TEMPLATES updated values."""

    def test_saludo_template_is_plain_text(self):
        """saludo template should be simpler, less emoji-heavy."""
        template = RESPONSE_TEMPLATES["saludo"]
        # The new template should still greet and ask what they're looking for
        assert "Onnix SA" in template
        assert "?" in template  # ends with a question

    def test_saludo_template_introduces_bot(self):
        """M2.F7: saludo presenta al bot como Onnix, el asistente virtual."""
        template = RESPONSE_TEMPLATES["saludo"]
        assert "Onnix" in template
        assert "asistente virtual" in template.lower()

    def test_lead_template_is_concise(self):
        """lead template should be concise (under 150 chars)."""
        template = RESPONSE_TEMPLATES["lead"]
        assert len(template) < 150, f"lead template too long: {len(template)} chars"

    def test_all_template_keys_still_present(self):
        """All 10 RESPONSE_TEMPLATES keys must be present (lead_con_nombre added in M2.F7)."""
        expected_keys = [
            "saludo", "busqueda", "busqueda_incompleta", "paginacion",
            "detalle", "lead", "lead_con_nombre", "ambiguo_visita",
            "elegir_zona", "conversacion",
        ]
        for key in expected_keys:
            assert key in RESPONSE_TEMPLATES, f"Missing template key: {key}"


# ===========================================================================
# M2.F7 — Identidad Onnix + guards anti-regresión
# ===========================================================================


class TestNovaIdentityGuards:
    """Permanent guards ensuring the bot's identity stays generic.

    CERO nombres propios en templates ni system prompt. CERO promesas de
    tiempo. Si estos tests fallan en el futuro, algun cambio introdujo
    un hardcode prohibido.
    """

    _FORBIDDEN_NAMES = (
        "admin", "admin", "apellido",
        "ez careaga", "careaga", "asesor",
    )
    _FORBIDDEN_TIME_PROMISES = (
        "en breve",
        "a la brevedad",
        "enseguida",
        "en unos minutos",
        "en unos momentos",
        "en un ratito",
    )

    def test_no_proper_names_in_response_templates(self):
        for key, text in RESPONSE_TEMPLATES.items():
            low = text.lower()
            for name in self._FORBIDDEN_NAMES:
                assert name not in low, (
                    f"RESPONSE_TEMPLATES[{key!r}] contains forbidden name "
                    f"{name!r}: {text!r}"
                )

    def test_no_proper_names_in_system_prompt(self):
        """System prompt template must not hardcode team member names."""
        low = SYSTEM_PROMPT_TEMPLATE.lower()
        for name in self._FORBIDDEN_NAMES:
            assert name not in low, (
                f"SYSTEM_PROMPT_TEMPLATE contains forbidden name {name!r}"
            )

    def test_no_time_promises_in_response_templates(self):
        for key, text in RESPONSE_TEMPLATES.items():
            low = text.lower()
            for phrase in self._FORBIDDEN_TIME_PROMISES:
                assert phrase not in low, (
                    f"RESPONSE_TEMPLATES[{key!r}] contains time promise "
                    f"{phrase!r}: {text!r}"
                )

    def test_system_prompt_forbids_proper_names_explicitly(self):
        """System prompt must instruct Claude to avoid proper names."""
        prompt = get_system_prompt()
        assert "NUNCA uses nombres propios" in prompt, (
            "System prompt should explicitly forbid proper names"
        )

    def test_system_prompt_forbids_time_promises_explicitly(self):
        """System prompt must instruct Claude to avoid time promises."""
        prompt = get_system_prompt()
        assert "NUNCA prometas tiempos" in prompt, (
            "System prompt should explicitly forbid time promises"
        )


class TestLeadTemplateWithName:
    """M2.F7: get_response_template('lead', nombre=X) returns named variant."""

    def test_lead_without_name_returns_generic(self):
        result = get_response_template("lead")
        assert "{nombre}" not in result
        assert "Le pasé tus datos al equipo" in result

    def test_lead_with_name_returns_named_variant(self):
        result = get_response_template("lead", nombre="Juan")
        assert "Juan" in result
        assert "{nombre}" not in result
        assert "Le pasé tus datos al equipo" in result

    def test_lead_with_empty_name_falls_back_to_generic(self):
        result = get_response_template("lead", nombre="")
        assert "{nombre}" not in result
        assert result == RESPONSE_TEMPLATES["lead"]

    def test_lead_with_whitespace_name_falls_back_to_generic(self):
        result = get_response_template("lead", nombre="   ")
        assert result == RESPONSE_TEMPLATES["lead"]


# ===========================================================================
# B7: descripcion_libre qualitative instruction
# ===========================================================================


class TestDescripcionLibreInstruction:
    """B7: System prompt must instruct Claude to use descripcion_libre for
    qualitative property characteristics mentioned by the user."""

    def test_descripcion_libre_instruction_present(self):
        """System prompt must contain instruction to use descripcion_libre."""
        assert "descripcion_libre" in SYSTEM_PROMPT_TEMPLATE

    def test_qualitative_examples_present(self):
        """Instruction must include concrete qualitative examples (patio or pileta)."""
        assert "patio" in SYSTEM_PROMPT_TEMPLATE or "pileta" in SYSTEM_PROMPT_TEMPLATE

    def test_instruction_section_header(self):
        """Prompt must have the qualitative characteristics section header."""
        assert "Características cualitativas" in SYSTEM_PROMPT_TEMPLATE

    def test_instruction_explains_automatic_processing(self):
        """Instruction must tell Claude the system processes the field automatically."""
        prompt = get_system_prompt()
        assert "automáticamente" in prompt or "automaticamente" in prompt

    def test_instruction_not_waiting_for_explicit_request(self):
        """Instruction must tell Claude not to wait for user to explicitly request it."""
        prompt = get_system_prompt()
        assert "explícitamente" in prompt or "explicitamente" in prompt


# ===========================================================================
# Fase 6: get_opt_out_text — DB-backed opt-out text with fallback
# ===========================================================================

_EXPECTED_OPT_OUT = (
    "Entendido, no te vamos a escribir más.\n"
    "\n"
    "Si en algún momento querés retomar la búsqueda, escribinos cuando quieras."
)


class TestGetOptOutText:
    """Verifies get_opt_out_text resolves from DB or falls back to constant."""

    @pytest.mark.asyncio
    async def test_get_opt_out_text_returns_db_value_when_set(self):
        """Returns DB value when bot_setting_repo.get_value returns non-empty string."""
        mock_session = AsyncMock()
        with patch(
            "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
            new=AsyncMock(return_value="custom message"),
        ):
            result = await get_opt_out_text(mock_session)
        assert result == "custom message"

    @pytest.mark.asyncio
    async def test_get_opt_out_text_falls_back_when_value_empty(self):
        """Returns DEFAULT_OPT_OUT_TEXT when DB returns empty string."""
        mock_session = AsyncMock()
        with patch(
            "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
            new=AsyncMock(return_value=""),
        ):
            result = await get_opt_out_text(mock_session)
        assert result == DEFAULT_OPT_OUT_TEXT

    @pytest.mark.asyncio
    async def test_get_opt_out_text_falls_back_when_value_whitespace_only(self):
        """Returns DEFAULT_OPT_OUT_TEXT when DB returns whitespace-only string."""
        mock_session = AsyncMock()
        with patch(
            "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
            new=AsyncMock(return_value="   \n\t"),
        ):
            result = await get_opt_out_text(mock_session)
        assert result == DEFAULT_OPT_OUT_TEXT

    @pytest.mark.asyncio
    async def test_get_opt_out_text_falls_back_when_value_missing(self):
        """Returns DEFAULT_OPT_OUT_TEXT when DB returns None."""
        mock_session = AsyncMock()
        with patch(
            "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
            new=AsyncMock(return_value=None),
        ):
            result = await get_opt_out_text(mock_session)
        assert result == DEFAULT_OPT_OUT_TEXT

    def test_default_opt_out_text_matches_user_spec(self):
        """DEFAULT_OPT_OUT_TEXT matches the exact user-specified text (drift guard)."""
        assert DEFAULT_OPT_OUT_TEXT == _EXPECTED_OPT_OUT


# ===========================================================================
# Fase 13: get_ai_dual_fail_text — DB-backed dual-fail fallback with fallback
# ===========================================================================

_EXPECTED_AI_DUAL_FAIL = (
    "Perdón, estoy teniendo un problema técnico. Intentá de nuevo en unos minutos. "
    "Si es urgente escribí ASESOR y te contactamos."
)


class TestGetAiDualFailText:
    """Verifies get_ai_dual_fail_text resolves from DB or falls back to constant."""

    @pytest.mark.asyncio
    async def test_returns_db_value_when_set(self):
        """Returns DB value when bot_setting_repo.get_value returns non-empty string."""
        mock_session = AsyncMock()
        with patch(
            "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
            new=AsyncMock(return_value="Mensaje personalizado de fallo AI"),
        ):
            result = await get_ai_dual_fail_text(mock_session)
        assert result == "Mensaje personalizado de fallo AI"

    @pytest.mark.asyncio
    async def test_falls_back_when_value_empty(self):
        """Returns DEFAULT_AI_DUAL_FAIL_TEXT when DB returns empty string."""
        mock_session = AsyncMock()
        with patch(
            "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
            new=AsyncMock(return_value=""),
        ):
            result = await get_ai_dual_fail_text(mock_session)
        assert result == DEFAULT_AI_DUAL_FAIL_TEXT

    @pytest.mark.asyncio
    async def test_falls_back_when_value_whitespace_only(self):
        """Returns DEFAULT_AI_DUAL_FAIL_TEXT when DB returns whitespace-only string."""
        mock_session = AsyncMock()
        with patch(
            "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
            new=AsyncMock(return_value="   \n\t"),
        ):
            result = await get_ai_dual_fail_text(mock_session)
        assert result == DEFAULT_AI_DUAL_FAIL_TEXT

    @pytest.mark.asyncio
    async def test_falls_back_when_value_missing(self):
        """Returns DEFAULT_AI_DUAL_FAIL_TEXT when DB returns None."""
        mock_session = AsyncMock()
        with patch(
            "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
            new=AsyncMock(return_value=None),
        ):
            result = await get_ai_dual_fail_text(mock_session)
        assert result == DEFAULT_AI_DUAL_FAIL_TEXT

    def test_default_ai_dual_fail_text_matches_user_spec(self):
        """DEFAULT_AI_DUAL_FAIL_TEXT matches the exact user-specified text (drift guard)."""
        assert DEFAULT_AI_DUAL_FAIL_TEXT == _EXPECTED_AI_DUAL_FAIL


# ===========================================================================
# M3 Fase C — Few-shot canonical examples in system prompt
# ===========================================================================


class TestCanonicalExamples:
    """M3 Fase C: verifica que los 3 ejemplos canónicos están presentes en el
    prompt generado y sobreviven cambios futuros al template.

    Cada test ancla su ejemplo con strings literales del template para
    detectar regresiones si alguien edita o borra la sección.
    """

    def test_prompt_contains_example_merge_filtros(self):
        """Ejemplo 1: merge parcial de filtros debe estar en el prompt."""
        prompt = get_system_prompt()
        assert "Ejemplo 1" in prompt
        assert "acumular" in prompt.lower() or "merge" in prompt.lower()
        assert "Villa Morra" in prompt  # ancla semántica del ejemplo 1

    def test_prompt_contains_example_filtro_ambiguo(self):
        """Ejemplo 2: cuantificador ambiguo debe estar en el prompt."""
        prompt = get_system_prompt()
        assert "Ejemplo 2" in prompt
        assert "máximo 2 habitaciones" in prompt
        assert "aclara" in prompt.lower() or "pregunt" in prompt.lower()

    def test_prompt_contains_example_sin_resultados(self):
        """Ejemplo 3: sin resultados proactivo debe estar en el prompt."""
        prompt = get_system_prompt()
        assert "Ejemplo 3" in prompt
        assert "0 propiedades" in prompt or "sin resultados" in prompt.lower()
        assert "esperar" in prompt.lower()


# ===========================================================================
# TONE-01 (2026-06) — Tono cálido: política 1-emoji + cierre sin "cuando puedan"
# ===========================================================================


class TestToneWarmth:
    """TONE-01: cliente reportó el bot 'muy frío, sin emojis'.

    Se calienta el tono en AMBOS prompts (busqueda + recepcionista) SIN
    regresionar las reglas duras: máximo 1 emoji por mensaje, cierre de lead
    cálido y de acción (sin el frío 'cuando puedan'), prohibición de prometer
    tiempos intacta, respuestas cortas, sin risas.
    """

    _MODES = ("busqueda", "recepcionista")

    def _all_exported_prompts(self) -> dict[str, str]:
        """Every prompt/template surface the bot can emit text from."""
        surfaces: dict[str, str] = {
            f"system[{m}]": get_system_prompt(mode=m) for m in self._MODES
        }
        surfaces.update(
            {f"gemini[{m}]": get_gemini_system_prompt(mode=m) for m in self._MODES}
        )
        surfaces.update(
            {f"template[{k}]": v for k, v in RESPONSE_TEMPLATES.items()}
        )
        surfaces["opt_out"] = DEFAULT_OPT_OUT_TEXT
        surfaces["ai_dual_fail"] = DEFAULT_AI_DUAL_FAIL_TEXT
        return surfaces

    def test_prompt_emoji_policy_present(self):
        """Both modes carry the max-1-emoji policy (with do/don't guidance)."""
        for mode in self._MODES:
            prompt = get_system_prompt(mode=mode)
            assert "1 emoji" in prompt, (
                f"mode={mode}: emoji policy ('1 emoji' max per message) missing"
            )
            low = prompt.lower()
            # Guardrail: emojis never next to prices/legal/bad news.
            assert "precios" in low and "emoji" in low, (
                f"mode={mode}: emoji policy must forbid emojis next to prices"
            )

    def test_prompt_no_cuando_puedan(self):
        """The cold closing 'cuando puedan' is gone from every exported surface."""
        for name, text in self._all_exported_prompts().items():
            assert "cuando puedan" not in text.lower(), (
                f"{name} still contains the cold phrase 'cuando puedan'"
            )

    def test_prompt_hard_rules_intact(self):
        """Warming the tone must NOT relax the hard rules in either mode."""
        for mode in self._MODES:
            prompt = get_system_prompt(mode=mode)
            # No time promises — rule and its examples stay.
            assert "NUNCA prometas tiempos" in prompt, f"mode={mode}"
            assert "en breve" in prompt and "a la brevedad" in prompt, f"mode={mode}"
            # Short replies (2-3) stay.
            assert "2-3" in prompt, f"mode={mode}: short-replies rule missing"
            # No laughs stays.
            assert "NUNCA uses risas" in prompt and "jaja" in prompt, f"mode={mode}"
            # No repetitive filler stays.
            assert "NUNCA uses expresiones" in prompt, f"mode={mode}"

    def test_prompt_warm_closing_example_present(self):
        """Both modes show the warm action-commitment closing style."""
        for mode in self._MODES:
            prompt = get_system_prompt(mode=mode)
            assert "le paso tus datos al equipo para que te contacten" in prompt.lower(), (
                f"mode={mode}: warm action-commitment closing example missing"
            )


# ===========================================================================
# M5.1 Paso 1 — Geography hint (top-5 cities + resolver_zona)
# ===========================================================================


class TestGeographyHint:
    """M5.1: verifica que el hint de las 5 ciudades top está en el prompt y
    que el bloque de geografía hardcodeada fue reemplazado."""

    def test_prompt_contains_top5_cities_hint(self):
        """System prompt debe contener el hint de las 5 ciudades con más oferta."""
        prompt = get_system_prompt()
        assert "Asunción" in prompt
        assert "Luque" in prompt
        assert "Encarnación" in prompt
        assert "San Bernardino" in prompt
        assert "San Lorenzo" in prompt

    def test_prompt_mentions_resolver_zona_tool(self):
        """System prompt debe mencionar resolver_zona para zonas ambiguas."""
        prompt = get_system_prompt()
        assert "resolver_zona" in prompt

    def test_prompt_has_zonas_section(self):
        """System prompt debe tener sección ## Zonas."""
        prompt = get_system_prompt()
        assert "## Zonas" in prompt

    def test_geo_data_path_ignored(self):
        """geo_data_path es deprecated; ambos calls retornan el mismo prompt."""
        prompt_with_path = get_system_prompt(os.environ["GEO_DATA_PATH"])
        prompt_without_path = get_system_prompt()
        assert prompt_with_path == prompt_without_path


# ---------------------------------------------------------------------------
# Fecha actual en el system prompt (fix QA staging: bot alucinaba fechas)
# ---------------------------------------------------------------------------

class TestFechaActualEnPrompt:
    """El LLM debe recibir la fecha actual (America/Asuncion) por request.

    QA staging: usuario dijo "este sábado a las 10:00" (jueves 2026-06-11)
    y el bot respondió "ese sábado ya pasó... ¿el 18 de enero?" — la fecha
    no se inyectaba en ningún lado y Claude razonaba desde su training.
    """

    def test_build_fecha_actual_line_formats_spanish_date(self):
        """Con un datetime fijo, la línea sale en español con día/mes/año correctos."""
        from datetime import datetime
        from app.bot.ai.prompts import build_fecha_actual_line
        from app.tz import PYT

        fixed = datetime(2026, 6, 11, 10, 30, tzinfo=PYT)  # jueves
        line = build_fecha_actual_line(fixed)
        assert line == "Hoy es jueves 11 de junio de 2026 (zona horaria Paraguay)."

    def test_build_fecha_actual_line_converts_to_paraguay_tz(self):
        """Un datetime UTC cerca de medianoche cae al día anterior en PYT (UTC-3/-4)."""
        from datetime import datetime, timezone as tz
        from app.bot.ai.prompts import build_fecha_actual_line

        # 2026-06-12 01:00 UTC == 2026-06-11 22:00 en Asunción (UTC-3)
        fixed = datetime(2026, 6, 12, 1, 0, tzinfo=tz.utc)
        line = build_fecha_actual_line(fixed)
        assert "11 de junio de 2026" in line

    def test_build_fecha_actual_line_defaults_to_now(self):
        """Sin argumento usa la fecha de hoy en PYT: año y mes actuales en español."""
        from datetime import datetime
        from app.bot.ai.prompts import build_fecha_actual_line, _MESES_ES
        from app.tz import PYT

        now = datetime.now(PYT)
        line = build_fecha_actual_line()
        assert line.startswith("Hoy es ")
        assert str(now.year) in line
        assert f" de {_MESES_ES[now.month - 1]} de " in line
        assert "(zona horaria Paraguay)" in line

    def test_build_dynamic_prompt_injects_fecha_in_dynamic_block(self):
        """La fecha va en el bloque dinámico (block 1), NUNCA en el bloque base
        cacheado (block 0) — la fecha cambia por día y el base lleva cache_control."""
        from datetime import datetime
        from app.bot.ai.prompt_builder import build_dynamic_prompt
        from app.tz import PYT

        state = ConversationState()
        result = build_dynamic_prompt("BASE_PROMPT", state)

        assert result[0]["text"] == "BASE_PROMPT"
        assert "cache_control" in result[0]
        assert "Hoy es" not in result[0]["text"]

        assert len(result) >= 2, "dynamic block with the date must always exist"
        assert "Hoy es" in result[1]["text"]
        assert str(datetime.now(PYT).year) in result[1]["text"]
        assert "cache_control" not in result[1]

    async def test_call_gemini_injects_fecha_in_system_prompt(self):
        """El fallback Gemini recibe la misma fecha actual en su system prompt."""
        from datetime import datetime
        from app.bot.ai.gemini_fallback import call_gemini
        from app.bot.ai.types import AIResponse
        from app.tz import PYT

        gemini = AsyncMock()
        gemini.send_message = AsyncMock(
            return_value=AIResponse(text="r", model="gemini-flash")
        )
        await call_gemini(gemini, "SYS_GEMINI", [], "hola")

        system = gemini.send_message.call_args[1]["system"]
        assert system.startswith("SYS_GEMINI")
        assert "Hoy es" in system
        assert str(datetime.now(PYT).year) in system

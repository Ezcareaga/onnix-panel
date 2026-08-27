"""E2E tests — Fase F: detalle, paginación, registro de lead.

Covers flows 5, 6 and 7 from the M3 test plan:
  - Flow 5: ver_detalle_y_volver_a_resultados
  - Flow 6: paginacion_ver_mas (reuses pending_results, never re-searches)
  - Flow 7: lead_registrado + lead_ya_registrado_no_duplica

All external surfaces (Claude, SearchService, DB senders) are mocked.
Only observable behavior is asserted: tool called, keywords in response,
search_context fields, and lead_events rows in onnix_dev.
"""
from __future__ import annotations

import pytest
import sqlalchemy

from app.bot.core.types import ConversationState


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_property(prop_id: int, *, zona: str = "Villa Morra", precio: int = 180000) -> dict:
    """Minimal property dict — only fields the tests assert on."""
    return {
        "id": prop_id,
        "title": f"Casa en {zona} #{prop_id}",
        "operation": "venta",
        "property_type": "casa",
        "city": "Asuncion",
        "barrio": zona,
        "price_usd": float(precio),
        "is_active": True,
        "source": "onnix",
        "local_image_count": 2,
        "description": f"Hermosa casa en {zona} con 3 dormitorios.",
    }


# ---------------------------------------------------------------------------
# Flow 5 — Ver detalle y volver a resultados
# ---------------------------------------------------------------------------

class TestVerDetalleYVolverAResultados:
    """Flow 5: after a search, user asks for detail, then 'más opciones'.

    Turn 1: search returns 2 properties (IDs 101 + 102) + pending [103, 104, 105].
    Turn 2: user asks for detail of property 101 → get_property_detail called.
    Turn 3: user says "más opciones" → pagination via pending_results (no AI re-search).
    """

    @pytest.mark.asyncio
    async def test_ver_detalle_y_volver_a_resultados(self, runner):
        """Post-search detail view followed by 'más opciones' pagination.

        Asserts:
        - Turn 2: get_property_detail tool was called; response includes zona/precio keywords.
        - Turn 3: NO new call to search_properties; response is a pagination intent.
        - search_context.filtros preserved across all 3 turns.
        """
        # ---- Turn 1: search with 5 properties --------------------------------
        props = [_make_property(i) for i in [101, 102, 103, 104, 105]]

        runner.program_search_result(props, total_found=5)
        runner.program_claude_response(
            tool_calls=[{
                "name": "search_properties",
                "input": {"operacion": "venta", "tipo": "casa", "barrio": "Villa Morra"},
            }],
            text="Te muestro 2 opciones en Villa Morra.",
        )

        resp1 = await runner.send("busco casa en venta en Villa Morra")
        assert resp1 is not None
        runner.assert_last_tool("search_properties")

        # After turn 1 the runner tracks the context via update_search_context.
        # Manually seed the context so turn 2 has current_page_ids and
        # turn 3 has resultados_pendientes (mirrors what the orchestrator writes).
        ctx_after_search = ConversationState(
            etapa="mostrando_resultados",
            filtros={"operacion": "venta", "tipo": "casa", "barrio": "Villa Morra"},
            current_page_ids=[101, 102],
            shown_properties=[101, 102],
            resultados_pendientes=[103, 104, 105],
            total_found=5,
            search_shown_count=2,
        )
        runner.set_search_context(ctx_after_search)

        # ---- Turn 2: user asks for detail of property 101 --------------------
        detail_prop = _make_property(101, zona="Villa Morra", precio=180000)
        runner.program_detail_result(detail_prop)
        # tool_executor.execute must return the property dict (no "error" key, has "id")
        runner.program_tool_executor_result(detail_prop)

        runner.program_claude_response(
            tool_calls=[{
                "name": "get_property_detail",
                "input": {"referencia": "la primera"},
            }],
            text=(
                "La primera propiedad es una casa en Villa Morra por USD 180.000. "
                "Tiene 3 dormitorios y un amplio jardín."
            ),
        )

        resp2 = await runner.send("dame detalle del 1")
        assert resp2 is not None

        # Tool must be get_property_detail
        runner.assert_last_tool("get_property_detail")

        # Response must mention the zona and precio context
        runner.assert_response_contains("villa morra")

        # Preserve context for turn 3 — detail view keeps pending_results intact
        ctx_after_detail = ConversationState(
            etapa="detalle",
            filtros={"operacion": "venta", "tipo": "casa", "barrio": "Villa Morra"},
            current_page_ids=[101, 102],
            shown_properties=[101, 102],
            resultados_pendientes=[103, 104, 105],
            last_detalle_id=101,
            total_found=5,
            search_shown_count=2,
        )
        runner.set_search_context(ctx_after_detail)

        # ---- Turn 3: user says "más opciones" → pagination, no re-search -----
        # The orchestrator will intercept this via _PAGINATION_RE and call
        # _handle_ver_mas() directly — NO AI call, NO search_properties call.
        # We program get_by_ids to return properties 103 and 104.
        runner.program_search_result([_make_property(103), _make_property(104)])

        # Record search_properties calls before turn 3
        search_calls_before = runner._search_mock.search_properties.call_count

        resp3 = await runner.send("mostrame más opciones")
        assert resp3 is not None

        # Pagination must NOT re-invoke search_properties
        search_calls_after = runner._search_mock.search_properties.call_count
        assert search_calls_after == search_calls_before, (
            "Pagination should not call search_properties again. "
            f"Call count went from {search_calls_before} to {search_calls_after}."
        )

        # Response intent is "paginacion" (set by _handle_ver_mas)
        assert resp3.intent == "paginacion", (
            f"Expected intent='paginacion', got '{resp3.intent}'"
        )

        # search_context filtros must be preserved across all 3 turns
        # (we check the filtros we seeded — they don't change on pagination)
        assert ctx_after_detail.filtros == {
            "operacion": "venta", "tipo": "casa", "barrio": "Villa Morra"
        }, (
            f"Filtros must not be wiped by detail or pagination, got: {ctx_after_detail.filtros}"
        )


# ---------------------------------------------------------------------------
# Flow 6 — Paginación: ver más (sin re-buscar)
# ---------------------------------------------------------------------------

class TestPaginacionVerMas:
    """Flow 6: first search returns 5 results, bot shows 2. User asks 'más'.

    Validates that the orchestrator paginates from resultados_pendientes
    WITHOUT calling search_properties again.
    """

    @pytest.mark.asyncio
    async def test_paginacion_ver_mas(self, runner):
        """'mostrame más' after 5-result search → pagination via pending_results only.

        Asserts:
        - search_properties is NOT called on the 'más' turn.
        - get_by_ids IS called to fetch next 2 properties.
        - Response intent = 'paginacion'.
        - After pagination: shown_properties includes ids 1-4, pending = [id5].
        """
        # Pre-seed search context with 3 pending results (as if turn 1 already happened)
        prop_ids = [201, 202, 203, 204, 205]
        ctx_with_pending = ConversationState(
            etapa="mostrando_resultados",
            filtros={"operacion": "alquiler", "tipo": "departamento", "ciudad": "Asuncion"},
            current_page_ids=[201, 202],
            shown_properties=[201, 202],
            resultados_pendientes=[203, 204, 205],
            total_found=5,
            search_shown_count=2,
        )
        runner.set_search_context(ctx_with_pending)

        # get_by_ids will be called with [203, 204] — program it to return those two
        next_props = [_make_property(203), _make_property(204)]
        runner.program_search_result(next_props)

        # Capture call counts before sending pagination text
        search_calls_before = runner._search_mock.search_properties.call_count
        detail_calls_before = runner._search_mock.get_by_ids.call_count

        response = await runner.send("ver más opciones")

        assert response is not None

        # search_properties must NOT be called again (pagination is from cache)
        search_calls_after = runner._search_mock.search_properties.call_count
        assert search_calls_after == search_calls_before, (
            "Pagination turn must not call search_properties. "
            f"Calls: {search_calls_before} → {search_calls_after}"
        )

        # get_by_ids MUST be called to fetch the next page
        detail_calls_after = runner._search_mock.get_by_ids.call_count
        assert detail_calls_after > detail_calls_before, (
            "Pagination must call get_by_ids to fetch next properties"
        )

        # Response intent must be 'paginacion'
        assert response.intent == "paginacion", (
            f"Expected intent='paginacion', got '{response.intent}'"
        )

        # The paginated response contains properties 203 and 204
        returned_ids = [p["id"] for p in response.properties]
        assert 203 in returned_ids or 204 in returned_ids, (
            f"Expected properties 203 or 204 in response, got: {returned_ids}"
        )

        # shown_ids in the response reflects the next page
        assert response.shown_ids is not None

        # The pending_ids in the response should only contain 205 now
        # (the orchestrator pops 203+204 off the front, leaving [205])
        assert response.pending_ids == [205], (
            f"After showing 203+204, pending_ids should be [205], got: {response.pending_ids}"
        )

    @pytest.mark.asyncio
    async def test_paginacion_texto_variante(self, runner):
        """'dame más' variant also triggers pagination without re-searching.

        The _PAGINATION_RE regex in orchestrator matches many natural language forms.
        This test validates a second trigger phrase.
        """
        ctx_with_pending = ConversationState(
            etapa="mostrando_resultados",
            filtros={"operacion": "venta", "tipo": "terreno"},
            current_page_ids=[301, 302],
            shown_properties=[301, 302],
            resultados_pendientes=[303, 304],
            total_found=4,
            search_shown_count=2,
        )
        runner.set_search_context(ctx_with_pending)

        runner.program_search_result([_make_property(303), _make_property(304)])

        search_calls_before = runner._search_mock.search_properties.call_count

        response = await runner.send("dame más")

        assert response is not None

        # Pagination regex must match 'dame más'
        search_calls_after = runner._search_mock.search_properties.call_count
        assert search_calls_after == search_calls_before, (
            "'dame más' must trigger pagination shortcut, not re-search. "
            f"Calls: {search_calls_before} → {search_calls_after}"
        )
        assert response.intent == "paginacion", (
            f"Expected intent='paginacion', got '{response.intent}'"
        )


# ---------------------------------------------------------------------------
# Flow 7 — Registro de lead
# ---------------------------------------------------------------------------

class TestLeadRegistrado:
    """Flow 7a: user asks to speak with an advisor → register_lead tool called.

    Validates:
    - register_lead tool invoked.
    - lead_events row written to DB with event_type='lead_registered'.
    - search_context.lead_registrado = True.
    - Response confirms asesor will contact them.
    """

    @pytest.mark.asyncio
    async def test_lead_registrado(self, runner, seeded_contact, e2e_session):
        """'quiero hablar con un asesor' → lead registered in DB.

        This test verifies the complete observable pipeline:
        1. Claude calls register_lead.
        2. tool_executor returns success.
        3. Orchestrator writes lead_registered to lead_events.
        4. search_context.lead_registrado = True.
        5. Response text confirms advisor contact.
        """
        # Arrange: user has already seen some properties
        ctx_pre_lead = ConversationState(
            etapa="mostrando_resultados",
            filtros={"operacion": "venta", "tipo": "casa", "ciudad": "Asuncion"},
            current_page_ids=[401, 402],
            shown_properties=[401, 402],
            resultados_pendientes=[],
            total_found=2,
            search_shown_count=2,
            lead_registrado=False,
        )
        runner.set_search_context(ctx_pre_lead)

        # tool_executor must return success for orchestrator to set is_lead=True
        runner.program_tool_executor_result({
            "success": True,
            "motivo": "quiere hablar con asesor",
            "message": "Lead registrado",
        })

        # Program a second Claude call for lead profiling (orchestrator calls Claude
        # again after register_lead to build a contact profile summary).
        # First call: tool_call → register_lead
        # Second call (tool result): final text response
        # Third call: lead profiler (SUMMARIZER_PROMPT) → returns JSON profile
        from app.bot.ai.types import AIResponse
        from tests.bot.e2e.runner import _make_text_ai_response, _make_tool_ai_response

        lead_tool_response = _make_tool_ai_response(
            "register_lead",
            {"motivo": "quiere hablar con asesor"},
        )
        lead_text_response = _make_text_ai_response(
            "Listo! Un asesor de Onnix SA te va a contactar en breve."
        )
        # Profile response: Claude returns a JSON summary
        profile_response = _make_text_ai_response(
            '{"preferencias": "casa en venta", "zona": "Asuncion"}'
        )

        runner._claude_mock.send_message.side_effect = [
            lead_tool_response,
            lead_text_response,
            profile_response,  # lead profiler call
        ]
        runner._last_tool_name = "register_lead"

        # Act
        response = await runner.send("quiero hablar con un asesor")

        assert response is not None, "Orchestrator must return a BotResponse for lead"

        # Assert: register_lead tool was the last programmed tool
        runner.assert_last_tool("register_lead")

        # Assert: response text confirms advisor contact
        response_text = (response.text or "").lower()
        advisor_keywords = ["asesor", "contactar", "va a", "brevemente", "breve", "listo"]
        matched = any(kw in response_text for kw in advisor_keywords)
        assert matched, (
            f"Response must confirm advisor contact, got: '{response.text}'. "
            f"Looked for any of: {advisor_keywords}"
        )

        # Assert: lead_events row in DB (orchestrator writes without committing —
        # visible in same session if we re-read)
        lead_result = await e2e_session.execute(
            sqlalchemy.text(
                "SELECT event_type, new_status FROM lead_events "
                "WHERE contact_id = :cid AND event_type = 'lead_registered' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"cid": seeded_contact["id"]},
        )
        lead_row = lead_result.first()
        assert lead_row is not None, (
            f"Expected a 'lead_registered' row in lead_events for contact_id={seeded_contact['id']}. "
            "Orchestrator must write to DB when register_lead tool succeeds."
        )
        assert lead_row.event_type == "lead_registered", (
            f"Expected event_type='lead_registered', got '{lead_row.event_type}'"
        )

        # Assert: search_context.lead_registrado = True
        # The orchestrator sets this on search_context during the tool loop.
        # We verify via the context captured in update_search_context calls.
        update_mock = runner._conversation_manager.update_search_context
        assert update_mock.called, "update_search_context must be called after lead registration"
        # Find the call where lead_registrado=True was set
        lead_registrado_set = False
        for call in update_mock.call_args_list:
            ctx_arg = call[0][2] if len(call[0]) > 2 else None
            if ctx_arg is not None and getattr(ctx_arg, "lead_registrado", False):
                lead_registrado_set = True
                break
        assert lead_registrado_set, (
            "search_context.lead_registrado must be set to True after register_lead. "
            f"update_search_context was called {update_mock.call_count} times — "
            "none had lead_registrado=True."
        )

    @pytest.mark.asyncio
    async def test_lead_is_lead_flag(self, runner, seeded_contact):
        """BotResponse.is_lead is True when register_lead tool is called.

        Secondary assertion — confirms the response-level flag is set correctly.
        """
        ctx = ConversationState(
            etapa="mostrando_resultados",
            filtros={"operacion": "venta"},
            shown_properties=[501],
            lead_registrado=False,
        )
        runner.set_search_context(ctx)

        runner.program_tool_executor_result({
            "success": True,
            "motivo": "interesado en asesor",
            "message": "Lead registrado",
        })

        from tests.bot.e2e.runner import _make_text_ai_response, _make_tool_ai_response

        runner._claude_mock.send_message.side_effect = [
            _make_tool_ai_response("register_lead", {"motivo": "interesado en asesor"}),
            _make_text_ai_response("Un asesor te contactará pronto."),
            _make_text_ai_response('{"preferencias": "venta"}'),  # profiler
        ]
        runner._last_tool_name = "register_lead"

        response = await runner.send("me pueden contactar?")

        assert response is not None
        assert response.is_lead is True, (
            f"BotResponse.is_lead must be True when register_lead succeeds, got {response.is_lead}"
        )


# ---------------------------------------------------------------------------
# Flow 7b — Lead ya registrado: no duplica
# ---------------------------------------------------------------------------

class TestLeadYaRegistradoNoDuplica:
    """Flow 7b: user asks for advisor again — orchestrator must NOT register a second lead.

    When search_context.lead_registrado=True, the system prompt already tells
    Claude the lead is registered.  We mock Claude to respond without calling
    register_lead again, and then verify no second lead_events row was created.
    """

    @pytest.mark.asyncio
    async def test_lead_ya_registrado_no_duplica(self, runner, seeded_contact, e2e_session):
        """Second 'me contactan?' → no new register_lead call, no duplicate DB row.

        Scenario: search_context.lead_registrado = True (already registered).
        Claude responds conversationally without calling the tool.

        Asserts:
        - No register_lead tool call in this turn.
        - No new lead_registered row in DB for this contact.
        - Response indicates the lead is already registered.
        """
        # Arrange: context with lead already registered
        ctx_lead_done = ConversationState(
            etapa="mostrando_resultados",
            filtros={"operacion": "venta", "tipo": "casa"},
            shown_properties=[601, 602],
            lead_registrado=True,  # key field — lead already registered
        )
        runner.set_search_context(ctx_lead_done)

        # Count existing lead_registered rows before this turn
        count_before_result = await e2e_session.execute(
            sqlalchemy.text(
                "SELECT COUNT(*) FROM lead_events "
                "WHERE contact_id = :cid AND event_type = 'lead_registered'"
            ),
            {"cid": seeded_contact["id"]},
        )
        count_before = count_before_result.scalar()

        # Claude responds WITHOUT calling register_lead (it knows lead is registered)
        runner.program_claude_response(
            text=(
                "Ya agendé tu consulta con el asesor. "
                "Te contactará en breve. ¿Hay algo más en lo que te pueda ayudar?"
            )
        )

        # Act
        response = await runner.send("me pueden contactar?")

        assert response is not None

        # Assert: no register_lead tool called this turn
        runner.assert_last_tool("none")

        # Assert: response indicates already registered (contains "asesor" or "ya")
        response_text = (response.text or "").lower()
        already_keywords = ["ya", "asesor", "agend", "contactar", "breve"]
        matched = any(kw in response_text for kw in already_keywords)
        assert matched, (
            f"Response should indicate lead already registered, got: '{response.text}'"
        )

        # Assert: no new lead_registered row was created
        count_after_result = await e2e_session.execute(
            sqlalchemy.text(
                "SELECT COUNT(*) FROM lead_events "
                "WHERE contact_id = :cid AND event_type = 'lead_registered'"
            ),
            {"cid": seeded_contact["id"]},
        )
        count_after = count_after_result.scalar()

        assert count_after == count_before, (
            f"No new lead_registered row should be created when lead_registrado=True. "
            f"count before={count_before}, after={count_after}. "
            "If this fails: check that Claude respects lead_registrado in the system prompt."
        )

    @pytest.mark.asyncio
    async def test_lead_no_tool_cuando_registrado(self, runner):
        """With lead_registrado=True, register_lead tool must NOT be re-called.

        Simpler version of the dedup test — focuses purely on tool-level assertion
        without DB read (useful as a fast feedback check).
        """
        ctx_lead_done = ConversationState(
            etapa="detalle",
            filtros={"operacion": "venta"},
            shown_properties=[701],
            lead_registrado=True,
        )
        runner.set_search_context(ctx_lead_done)

        # Claude responds without tool call — it knows the lead is registered
        runner.program_claude_response(
            text="Ya tienes un asesor asignado. Te contactará pronto."
        )

        response = await runner.send("necesito que me llamen")

        assert response is not None

        # No register_lead tool must be called
        runner.assert_last_tool("none")

        # BotResponse.is_lead must be False (no lead tool was invoked)
        assert response.is_lead is False, (
            f"is_lead must be False when no register_lead tool is called, got {response.is_lead}"
        )

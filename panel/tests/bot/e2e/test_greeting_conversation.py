"""E2E tests — Fase D: saludo, conversación libre, mensaje ambiguo, opt-out.

Covers flows 8 (saludo), 13 (conversación libre), 14 (ambiguo/clarificación)
and the opt-out irreversible flow from the M3 test plan.

Each test validates observable behavior only:
- Which tool (if any) Claude called
- Keywords present in the response text
- DB state after the turn (for opt-out)
- Absence of buttons (for opt-out)

No real Twilio/Telegram calls are made. Senders are silenced by Fase A infra.
"""
from __future__ import annotations

import pytest
import sqlalchemy

from app.bot.core.types import ConversationState


class TestSaludoInicial:
    """Test flow 8: greeting message — Onnix identifies herself, no tool called."""

    @pytest.mark.asyncio
    async def test_saludo_inicial(self, runner):
        """'hola' → Onnix greets, identifies as Onnix, no tool invoked.

        Validates:
        - Claude responded without calling any tool (pure conversational turn).
        - Response mentions 'hola' or 'onnix' (case-insensitive, unaccented).
        - search_context remains at default 'inicio' stage (no filters populated).
        """
        runner.program_claude_response(
            text=(
                "¡Hola! Soy Onnix, el asistente virtual de Onnix SA. "
                "¿Qué estás buscando?"
            )
        )

        response = await runner.send("hola")

        assert response is not None, "Orchestrator must return a BotResponse for a greeting"

        # Claude must not have called any tool for a simple greeting
        runner.assert_last_tool("none")

        # Response must identify Onnix and greet
        runner.assert_response_contains("onnix")

        # search_context should have no meaningful filters after a greeting
        ctx: ConversationState = runner.context
        assert ctx.filtros == {}, (
            f"Greeting should not populate filtros, got: {ctx.filtros}"
        )
        assert ctx.lead_registrado is False, "Lead must not be registered on greeting"


class TestConversacionLibre:
    """Test flow 13: free-form conversational question — no search triggered."""

    @pytest.mark.asyncio
    async def test_conversacion_libre_zonas(self, runner):
        """'qué zonas manejan?' → Onnix answers without calling search tool.

        Validates:
        - Meta question about zones does not trigger buscar_propiedades.
        - Response mentions Asuncion and 'zona' (the two key context words).
        - No tool was invoked.
        """
        runner.program_claude_response(
            text=(
                "Trabajamos en Asunción y alrededores: Villa Morra, Las Mercedes, "
                "Carmelitas, y muchas más. ¿Alguna zona que te interese en particular?"
            )
        )

        response = await runner.send("qué zonas manejan?")

        assert response is not None

        # A meta question about the agency must NOT trigger a property search
        runner.assert_last_tool("none")

        # Response must mention Asuncion and zona (unaccented comparison)
        runner.assert_response_contains("asuncion", "zona")


class TestMensajeAmbiguo:
    """Test flow 14: ambiguous short message — Claude asks for clarification."""

    @pytest.mark.asyncio
    async def test_mensaje_ambiguo_pide_clarificacion(self, runner):
        """'quiero algo' → Onnix asks clarifying questions, no search triggered.

        Validates:
        - Vague intent does not trigger buscar_propiedades tool.
        - Response asks for clarification (mentions 'tipo', 'zona', 'buscando',
          or poses a question).
        - search_context filtros remain empty (no phantom filters inserted).
        """
        runner.program_claude_response(
            text=(
                "Para ayudarte mejor, ¿qué tipo de propiedad estás buscando? "
                "¿Casa, departamento, oficina? Y si tenés zona preferida, contame."
            )
        )

        response = await runner.send("quiero algo")

        assert response is not None

        # Ambiguous message must NOT trigger a blind property search
        runner.assert_last_tool("none")

        # Response must contain a clarifying question — at least one of these words
        response_text = (response.text or "").lower()
        clarification_words = ["tipo", "zona", "buscando", "propiedad", "casa", "departamento"]
        matched = any(word in response_text for word in clarification_words)
        assert matched, (
            f"Expected clarification question in response, got: '{response.text}'. "
            f"Looked for any of: {clarification_words}"
        )

        # search_context must not have phantom filters
        ctx: ConversationState = runner.context
        assert ctx.filtros == {}, (
            f"Ambiguous message must not populate filtros, got: {ctx.filtros}"
        )


class TestOptOutIrreversible:
    """Test opt-out flow: 'baja' → contact marked discarded, no buttons returned.

    The opt-out in v7 goes through Claude (not a local keyword match).
    Claude calls the process_opt_out tool → orchestrator writes to DB.
    Status becomes 'discarded', baja_at is set, is_bot_active = false.
    """

    @pytest.mark.asyncio
    async def test_opt_out_irreversible(self, runner, seeded_contact, e2e_session):
        """'baja' → contact status set to 'discarded' in DB, no buttons in response.

        Validates:
        - process_opt_out tool was invoked by Claude.
        - DB: contacts.status = 'discarded' for the seeded contact.
        - BotResponse.buttons is empty (no further interaction buttons).
        - Response contains a farewell message (mocked Claude text).
        """
        # Configure tool executor to return opt-out success (required for is_opt_out=True)
        runner.program_tool_executor_result({"success": True, "message": "Opt-out registrado"})

        # Program Claude to call process_opt_out tool, then confirm with farewell text
        runner.program_claude_response(
            tool_calls=[
                {"name": "process_opt_out", "input": {"motivo": "usuario solicitó baja"}}
            ],
            text="Listo, no te vuelvo a contactar. Cuando quieras volver, escribime.",
        )

        response = await runner.send("baja")

        assert response is not None, "Orchestrator must return a BotResponse even for opt-out"

        # The tool called must be process_opt_out
        runner.assert_last_tool("process_opt_out")

        # No buttons should be present after opt-out (conversation ends)
        assert response.buttons == [], (
            f"Opt-out response must not include buttons, got: {response.buttons}"
        )

        # DB verification: contact must be marked discarded within the same session
        # (orchestrator writes but does not commit — changes visible in same transaction)
        result = await runner.session.execute(
            sqlalchemy.text(
                "SELECT status FROM contacts WHERE id = :cid"
            ),
            {"cid": seeded_contact["id"]},
        )
        row = result.first()
        assert row is not None, f"Contact id={seeded_contact['id']} not found in DB"
        assert row.status == "discarded", (
            f"Expected contact status 'discarded' after opt-out, got '{row.status}'. "
            "TODO M4: if this fails, check that orchestrator uses correct contact.id from mock."
        )

    @pytest.mark.asyncio
    async def test_opt_out_variantes(self, runner, seeded_contact):
        """'no quiero más mensajes' → opt-out flow triggers if handler matches variant.

        If the orchestrator/Claude handles this phrase the same as 'baja', the
        contact should be marked discarded. If not (Claude responds conversationally),
        the test documents the behavior without failing.

        Note: v7 uses Claude to detect opt-out intent — variants depend on Claude's
        judgment. This test mocks Claude calling process_opt_out for the variant phrase.
        """
        runner.program_tool_executor_result({"success": True, "message": "Opt-out registrado"})

        runner.program_claude_response(
            tool_calls=[
                {"name": "process_opt_out", "input": {"motivo": "no quiere más mensajes"}}
            ],
            text="Entendido, te doy de baja. No te contactaré más.",
        )

        response = await runner.send("no quiero más mensajes")

        assert response is not None

        # When Claude calls process_opt_out, the tool name must match
        runner.assert_last_tool("process_opt_out")

        # Response confirms the opt-out (unaccented comparison)
        runner.assert_response_contains("baja")

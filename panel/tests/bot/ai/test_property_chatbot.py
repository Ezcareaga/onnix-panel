"""TDD — property_chatbot.parse()

Tests mock ClaudeClient so no real API is called.
"""
from __future__ import annotations

import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock


def _make_ai_response(text: str):
    """Build a minimal AIResponse-like object with just .text."""
    obj = SimpleNamespace()
    obj.text = text
    return obj


# Mock path: parse() imports ClaudeClient lazily from bot.config / env
# We patch the module-level client inside property_chatbot.
_PATCH_TARGET = "app.bot.ai.property_chatbot.ClaudeClient"


class TestParseSuccess:
    @pytest.mark.asyncio
    async def test_parse_returns_parsed_dict_on_success(self):
        payload = {
            "property_type": "departamento",
            "operation": "venta",
            "neighborhood": "villa morra",
            "price_max": 200000,
            "currency": "USD",
        }
        ai_resp = _make_ai_response(json.dumps(payload))

        with patch(_PATCH_TARGET) as MockClient:
            instance = AsyncMock()
            instance.send_message = AsyncMock(return_value=ai_resp)
            MockClient.return_value = instance

            from app.bot.ai import property_chatbot
            result, error = await property_chatbot.parse(
                "departamentos en Villa Morra hasta 200k USD para compra"
            )

        assert error is None
        assert result is not None
        assert result["property_type"] == "departamento"
        assert result["price_max"] == 200000

    @pytest.mark.asyncio
    async def test_parse_strips_markdown_fences(self):
        payload = {"operation": "alquiler", "city": "asuncion"}
        fenced = f"```json\n{json.dumps(payload)}\n```"
        ai_resp = _make_ai_response(fenced)

        with patch(_PATCH_TARGET) as MockClient:
            instance = AsyncMock()
            instance.send_message = AsyncMock(return_value=ai_resp)
            MockClient.return_value = instance

            from app.bot.ai import property_chatbot
            result, error = await property_chatbot.parse("alquiler en Asuncion")

        assert error is None
        assert result["operation"] == "alquiler"

    @pytest.mark.asyncio
    async def test_parse_strips_plain_code_fence(self):
        """Handles ``` (no language specifier) fence."""
        payload = {"operation": "venta"}
        fenced = f"```\n{json.dumps(payload)}\n```"
        ai_resp = _make_ai_response(fenced)

        with patch(_PATCH_TARGET) as MockClient:
            instance = AsyncMock()
            instance.send_message = AsyncMock(return_value=ai_resp)
            MockClient.return_value = instance

            from app.bot.ai import property_chatbot
            result, error = await property_chatbot.parse("venta")

        assert error is None
        assert result["operation"] == "venta"


class TestParseFailures:
    @pytest.mark.asyncio
    async def test_parse_returns_error_on_claude_failure(self):
        with patch(_PATCH_TARGET) as MockClient:
            instance = AsyncMock()
            instance.send_message = AsyncMock(side_effect=Exception("API error"))
            MockClient.return_value = instance

            from app.bot.ai import property_chatbot
            result, error = await property_chatbot.parse("depto villa morra")

        assert result is None
        assert error is not None
        assert len(error) > 0

    @pytest.mark.asyncio
    async def test_parse_returns_error_on_invalid_json(self):
        ai_resp = _make_ai_response("No entiendo, please rephrase.")

        with patch(_PATCH_TARGET) as MockClient:
            instance = AsyncMock()
            instance.send_message = AsyncMock(return_value=ai_resp)
            MockClient.return_value = instance

            from app.bot.ai import property_chatbot
            result, error = await property_chatbot.parse("algo")

        assert result is None
        assert error is not None

    @pytest.mark.asyncio
    async def test_parse_rejects_non_dict_json(self):
        """JSON válido pero no-dict (ej: []) debe rechazarse con el error estándar."""
        ai_resp = _make_ai_response("[]")

        with patch(_PATCH_TARGET) as MockClient:
            instance = AsyncMock()
            instance.send_message = AsyncMock(return_value=ai_resp)
            MockClient.return_value = instance

            from app.bot.ai import property_chatbot
            result, error = await property_chatbot.parse("depto villa morra")

        assert result is None
        assert error == property_chatbot._ERROR_MSG

    @pytest.mark.asyncio
    async def test_parse_returns_error_on_empty_text_response(self):
        ai_resp = _make_ai_response(None)

        with patch(_PATCH_TARGET) as MockClient:
            instance = AsyncMock()
            instance.send_message = AsyncMock(return_value=ai_resp)
            MockClient.return_value = instance

            from app.bot.ai import property_chatbot
            result, error = await property_chatbot.parse("casas en luque")

        assert result is None
        assert error is not None


class TestParseNewFields:
    """M6.5 T1a — amenities whitelisted + barato + descripcion_libre."""

    async def _parse_with_mock(self, payload: dict, query: str = "query de prueba"):
        ai_resp = _make_ai_response(json.dumps(payload))
        with patch(_PATCH_TARGET) as MockClient:
            instance = AsyncMock()
            instance.send_message = AsyncMock(return_value=ai_resp)
            MockClient.return_value = instance

            from app.bot.ai import property_chatbot
            return await property_chatbot.parse(query)

    @pytest.mark.asyncio
    async def test_parse_extracts_amenities(self):
        result, error = await self._parse_with_mock(
            {"property_type": "casa", "amenities": ["piscina", "parrilla"]},
            "casa con piscina y parrilla",
        )
        assert error is None
        assert result["amenities"] == ["piscina", "parrilla"]

    @pytest.mark.asyncio
    async def test_parse_normalizes_accented_amenities(self):
        result, error = await self._parse_with_mock(
            {"amenities": ["Balcón"]},
            "depto con balcón",
        )
        assert error is None
        assert result["amenities"] == ["balcon"]

    @pytest.mark.asyncio
    async def test_parse_rejects_unknown_amenities(self):
        result, error = await self._parse_with_mock(
            {"amenities": ["piscina", "jacuzzi"]},
            "casa con piscina y jacuzzi",
        )
        assert error is None
        assert result["amenities"] == ["piscina"]

    @pytest.mark.asyncio
    async def test_parse_drops_amenities_key_when_all_rejected(self):
        result, error = await self._parse_with_mock(
            {"property_type": "casa", "amenities": ["jacuzzi", "sauna"]},
            "casa con jacuzzi",
        )
        assert error is None
        assert "amenities" not in result

    @pytest.mark.asyncio
    async def test_parse_extracts_barato_flag(self):
        result, error = await self._parse_with_mock(
            {"operation": "venta", "barato": True},
            "casa barata en luque",
        )
        assert error is None
        assert result["barato"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_value", ["yes", "true", 1, "barato", 0, None])
    async def test_parse_discards_non_bool_barato(self, bad_value):
        """Coercion estricta: solo bool True pasa; cualquier otro valor se descarta."""
        result, error = await self._parse_with_mock(
            {"operation": "venta", "barato": bad_value},
            "casa barata",
        )
        assert error is None
        assert "barato" not in result

    @pytest.mark.asyncio
    async def test_parse_discards_barato_false(self):
        """barato: false del LLM no aporta señal — se descarta la key."""
        result, error = await self._parse_with_mock(
            {"operation": "venta", "barato": False},
            "casa en luque",
        )
        assert error is None
        assert "barato" not in result

    @pytest.mark.asyncio
    async def test_parse_extracts_descripcion_libre(self):
        result, error = await self._parse_with_mock(
            {"city": "asuncion", "descripcion_libre": "con vista al río, luminoso"},
            "depto en asuncion con vista al río, luminoso",
        )
        assert error is None
        assert result["descripcion_libre"] == "con vista al río, luminoso"

    @pytest.mark.asyncio
    async def test_parse_discards_short_descripcion_libre(self):
        result, error = await self._parse_with_mock(
            {"city": "asuncion", "descripcion_libre": "ok"},
            "depto en asuncion",
        )
        assert error is None
        assert "descripcion_libre" not in result

    @pytest.mark.asyncio
    async def test_parse_strips_descripcion_libre_whitespace(self):
        result, error = await self._parse_with_mock(
            {"descripcion_libre": "  luminoso y moderno  "},
            "depto luminoso y moderno",
        )
        assert error is None
        assert result["descripcion_libre"] == "luminoso y moderno"

    @pytest.mark.asyncio
    async def test_parse_without_new_fields_unchanged(self):
        """Passthrough sin inventos: query 100% estructurada no gana keys nuevas."""
        payload = {
            "property_type": "departamento",
            "operation": "venta",
            "city": "asuncion",
            "bedrooms_min": 3,
            "price_max": 200000,
            "currency": "USD",
        }
        result, error = await self._parse_with_mock(
            payload, "depto 3 dorm asuncion 200k"
        )
        assert error is None
        assert result == payload
        assert "amenities" not in result
        assert "barato" not in result
        assert "descripcion_libre" not in result


class TestParseInputHandling:
    @pytest.mark.asyncio
    async def test_parse_truncates_long_queries(self):
        long_query = "x" * 600
        payload = {"operation": "venta"}
        ai_resp = _make_ai_response(json.dumps(payload))

        captured_messages = []

        async def capture_send(system, messages, **kwargs):
            captured_messages.extend(messages)
            return ai_resp

        with patch(_PATCH_TARGET) as MockClient:
            instance = AsyncMock()
            instance.send_message = capture_send
            MockClient.return_value = instance

            from app.bot.ai import property_chatbot
            await property_chatbot.parse(long_query)

        # The user message content must be at most 500 chars
        user_content = captured_messages[0]["content"]
        assert len(user_content) <= 500

    @pytest.mark.asyncio
    async def test_parse_passes_tracking_source(self):
        payload = {"operation": "venta"}
        ai_resp = _make_ai_response(json.dumps(payload))

        captured_kwargs: dict = {}

        async def capture_send(system, messages, **kwargs):
            captured_kwargs.update(kwargs)
            return ai_resp

        with patch(_PATCH_TARGET) as MockClient:
            instance = AsyncMock()
            instance.send_message = capture_send
            MockClient.return_value = instance

            from app.bot.ai import property_chatbot
            await property_chatbot.parse("casas en san lorenzo")

        assert captured_kwargs.get("_tracking_source") == "properties_chatbot"

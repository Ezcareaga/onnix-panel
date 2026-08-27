"""TDD — POST /api/properties/parse-query

Tests mock both the chatbot module and BotSettingRepository so no DB/API is hit.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


_CHATBOT_PARSE = "app.routes.properties.property_chatbot.parse"
_BOT_SETTING_GET_BOOL = "app.routes.properties.BotSettingRepository.get_bool"


class TestParseQueryValidation:
    async def test_parse_query_returns_400_on_empty(self, admin_client):
        resp = await admin_client.post(
            "/api/properties/parse-query",
            json={"query": ""},
        )
        assert resp.status_code == 400

    async def test_parse_query_returns_400_on_missing_key(self, admin_client):
        resp = await admin_client.post(
            "/api/properties/parse-query",
            json={},
        )
        assert resp.status_code == 400

    async def test_parse_query_returns_400_on_whitespace_only(self, admin_client):
        resp = await admin_client.post(
            "/api/properties/parse-query",
            json={"query": "   "},
        )
        assert resp.status_code == 400


class TestParseQuerySuccess:
    async def test_parse_query_calls_chatbot_with_user_text(self, admin_client):
        captured = {}

        async def mock_parse(query: str):
            captured["query"] = query
            return ({"operation": "venta"}, None)

        with (
            patch(_CHATBOT_PARSE, side_effect=mock_parse),
            patch(_BOT_SETTING_GET_BOOL, new=AsyncMock(return_value=True)),
        ):
            resp = await admin_client.post(
                "/api/properties/parse-query",
                json={"query": "departamentos en Villa Morra"},
            )

        assert resp.status_code == 200
        assert captured["query"] == "departamentos en Villa Morra"

    async def test_parse_query_returns_parsed_dict(self, admin_client):
        parsed = {"operation": "venta", "neighborhood": "villa morra", "price_max": 200000}

        with (
            patch(_CHATBOT_PARSE, new=AsyncMock(return_value=(parsed, None))),
            patch(_BOT_SETTING_GET_BOOL, new=AsyncMock(return_value=True)),
        ):
            resp = await admin_client.post(
                "/api/properties/parse-query",
                json={"query": "depto villa morra hasta 200k"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] is None
        assert data["parsed"]["operation"] == "venta"
        assert data["parsed"]["price_max"] == 200000


class TestParseQueryFailure:
    async def test_parse_query_returns_error_on_chatbot_failure(self, admin_client):
        with (
            patch(
                _CHATBOT_PARSE,
                new=AsyncMock(return_value=(None, "No pude entender la consulta.")),
            ),
            patch(_BOT_SETTING_GET_BOOL, new=AsyncMock(return_value=True)),
        ):
            resp = await admin_client.post(
                "/api/properties/parse-query",
                json={"query": "algo incomprensible"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["parsed"] is None
        assert "No pude entender" in data["error"]


class TestParseQueryFeatureFlag:
    async def test_parse_query_returns_503_when_disabled(self, admin_client):
        with patch(_BOT_SETTING_GET_BOOL, new=AsyncMock(return_value=False)):
            resp = await admin_client.post(
                "/api/properties/parse-query",
                json={"query": "departamentos en luque"},
            )
        assert resp.status_code == 503

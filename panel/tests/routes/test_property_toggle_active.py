"""TDD — POST /properties/{id}/toggle-active

Toggle is_active for a property. Admin-only. The detail template must
expose the button only for admins, and must require confirmation in the
UI before posting (hx-confirm).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


def _prop(is_active: bool = True, on_hold: bool = False) -> dict:
    return {
        "id": 42,
        "source": "infocasas",
        "external_id": "9001",
        "title": "Casa en Asuncion",
        "url": "https://infocasas.com.py/prop/9001",
        "price_usd": 100000,
        "price_pyg": None,
        "price_currency": "USD",
        "operation": "venta",
        "property_type": "casa",
        "city": "Asunción",
        "neighborhood": "Villa Morra",
        "bedrooms": 3,
        "bathrooms": 2,
        "parking": 1,
        "total_area_m2": 90,
        "construction_state": "usado",
        "description": "Casa.",
        "agent_name": "María",
        "agent_phone": None,
        "agent_whatsapp": None,
        "is_active": is_active,
        "on_hold": on_hold,
        "local_image_count": 0,
        "main_image_url": None,
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2025, 3, 1, tzinfo=timezone.utc),
        "last_scraped_at": datetime(2025, 3, 1, tzinfo=timezone.utc),
        "portal_listed_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "portal_expires_at": datetime(2025, 7, 1, tzinfo=timezone.utc),
        "photo_urls": [],
    }


class TestToggleActiveAuth:
    async def test_unauthenticated_redirects_to_login(self, client):
        resp = await client.post("/properties/42/toggle-active")
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    async def test_non_admin_user_gets_403(self, user_client):
        with patch(
            "app.routes.properties.property_service.set_active",
            new=AsyncMock(return_value=False),
        ) as mock_set:
            resp = await user_client.post("/properties/42/toggle-active")
        assert resp.status_code == 403
        # Must not have called the service for non-admins
        mock_set.assert_not_called()


class TestToggleActiveBehavior:
    async def test_admin_can_deactivate_active_property(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=_prop(is_active=True)),
        ), patch(
            "app.routes.properties.property_service.set_active",
            new=AsyncMock(return_value=False),
        ) as mock_set:
            resp = await admin_client.post(
                "/properties/42/toggle-active",
                follow_redirects=False,
            )
        # Service must be called with the inverted value
        mock_set.assert_awaited_once()
        args, kwargs = mock_set.call_args
        # Accept (db, 42, False) or kwargs
        called_value = args[2] if len(args) >= 3 else kwargs.get("is_active")
        assert called_value is False
        # Redirect back to detail page
        assert resp.status_code in (200, 303)

    async def test_admin_can_reactivate_inactive_property(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=_prop(is_active=False)),
        ), patch(
            "app.routes.properties.property_service.set_active",
            new=AsyncMock(return_value=True),
        ) as mock_set:
            resp = await admin_client.post(
                "/properties/42/toggle-active",
                follow_redirects=False,
            )
        mock_set.assert_awaited_once()
        args, kwargs = mock_set.call_args
        called_value = args[2] if len(args) >= 3 else kwargs.get("is_active")
        assert called_value is True
        assert resp.status_code in (200, 303)

    async def test_returns_404_when_property_missing(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=None),
        ):
            resp = await admin_client.post("/properties/9999/toggle-active")
        assert resp.status_code == 404


class TestToggleActiveButtonInDetailTemplate:
    """The detail page must expose the toggle button to admins with confirm."""

    async def test_admin_sees_toggle_button(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=_prop(is_active=True)),
        ):
            resp = await admin_client.get("/properties/42")
        assert resp.status_code == 200
        # Form action posts to the toggle endpoint
        assert b"/properties/42/toggle-active" in resp.content

    async def test_non_admin_does_not_see_toggle_button(self, user_client):
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=_prop(is_active=True)),
        ):
            resp = await user_client.get("/properties/42")
        assert resp.status_code == 200
        assert b"/properties/42/toggle-active" not in resp.content

    async def test_button_requires_confirmation(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=_prop(is_active=True)),
        ):
            resp = await admin_client.get("/properties/42")
        assert resp.status_code == 200
        # onsubmit/return confirm() OR hx-confirm — either is acceptable.
        body = resp.text
        has_confirm = (
            "hx-confirm=" in body
            or "onsubmit=\"return confirm" in body
            or "onsubmit='return confirm" in body
        )
        assert has_confirm, "Toggle button must require confirmation"

    async def test_active_property_shows_deactivate_label(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=_prop(is_active=True)),
        ):
            resp = await admin_client.get("/properties/42")
        # Spanish label hint — case-insensitive match
        body_lower = resp.text.lower()
        assert "desactivar" in body_lower

    async def test_inactive_property_shows_reactivate_label(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=_prop(is_active=False)),
        ):
            resp = await admin_client.get("/properties/42")
        body_lower = resp.text.lower()
        assert "reactivar" in body_lower

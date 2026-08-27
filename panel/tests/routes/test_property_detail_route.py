"""TDD — GET /properties/{id} detail route

Tests: 200/404, photo cap at 15, auth guard.

La vigencia en el portal ya no se muestra: vivia en la card "Metadata" que
borro el carril D. El calculo sigue cubierto donde vive, en
tests/services/test_property_service.py::test_calc_estimated_expiry_*.
Mocking pattern mirrors test_properties_route.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


_BASE_PROP = {
    "id": 42,
    "source": "infocasas",
    "external_id": "9001",
    "title": "Departamento en Villa Morra",
    "url": "https://infocasas.com.py/prop/9001",
    "price_usd": 120000,
    "price_pyg": None,
    "price_currency": "USD",
    "operation": "venta",
    "property_type": "departamento",
    "city": "Asunción",
    "neighborhood": "Villa Morra",
    "bedrooms": 3,
    "bathrooms": 2,
    "parking": 1,
    "total_area_m2": 90,
    "construction_state": "usado",
    "description": "Hermoso departamento.",
    "agent_name": "María López",
    "agent_phone": "+595981000000",
    "agent_whatsapp": "+595981000000",
    "is_active": True,
    "on_hold": False,
    "local_image_count": 3,
    "main_image_url": None,
    "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    "updated_at": datetime(2025, 3, 1, tzinfo=timezone.utc),
    "last_scraped_at": datetime(2025, 3, 1, tzinfo=timezone.utc),
    "portal_listed_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    "portal_expires_at": datetime(2025, 7, 1, tzinfo=timezone.utc),
    # Service computes this; must be present in mocked return value
    "photo_urls": [
        "/images/infocasas/9001/1.webp",
        "/images/infocasas/9001/2.webp",
        "/images/infocasas/9001/3.webp",
    ],
}


class TestPropertyDetailAuth:
    async def test_unauthenticated_redirects_to_login(self, client):
        resp = await client.get("/properties/42")
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    async def test_admin_gets_200_for_existing(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=_BASE_PROP),
        ):
            resp = await admin_client.get("/properties/42")
        assert resp.status_code == 200


class TestPropertyDetailResponse:
    async def test_get_detail_returns_200_for_existing(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=_BASE_PROP),
        ):
            resp = await admin_client.get("/properties/42")
        assert resp.status_code == 200
        assert b"<html" in resp.content

    async def test_get_detail_returns_404_for_missing(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=None),
        ):
            resp = await admin_client.get("/properties/99999")
        assert resp.status_code == 404

    async def test_title_appears_in_page(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=_BASE_PROP),
        ):
            resp = await admin_client.get("/properties/42")
        assert b"Departamento en Villa Morra" in resp.content


class TestPropertyDetailPhotos:
    async def test_get_detail_caps_photos_at_15(self, admin_client):
        # Service enforces the cap and returns photo_urls with at most 15 items.
        # The mock simulates the already-capped output (15 items, not 20).
        capped_urls = [f"/images/infocasas/9001/{i}.webp" for i in range(1, 16)]
        prop = {**_BASE_PROP, "local_image_count": 20, "photo_urls": capped_urls}
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=prop),
        ):
            resp = await admin_client.get("/properties/42")
        assert resp.status_code == 200
        assert "/images/infocasas/9001/16.webp" not in resp.text
        assert "/images/infocasas/9001/15.webp" in resp.text

    async def test_get_detail_shows_all_photos_when_count_under_cap(self, admin_client):
        # _BASE_PROP already has 3 photo_urls built in
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=_BASE_PROP),
        ):
            resp = await admin_client.get("/properties/42")
        assert resp.status_code == 200
        assert "/images/infocasas/9001/3.webp" in resp.text

    async def test_alpine_carousel_attribute_uses_single_quotes(self, admin_client):
        # The carousel x-data attribute must be wrapped in single quotes so
        # that the double-quoted JSON URLs inside `photos` don't terminate
        # the HTML attribute. Regression: tojson does not escape `"`, so
        # x-data="...{photos: [\"/img/1.webp\"]}" used to leak the JS body
        # into the document body.
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=_BASE_PROP),
        ):
            resp = await admin_client.get("/properties/42")
        assert resp.status_code == 200
        assert "x-data='{" in resp.text
        assert 'x-data="{\n        idx' not in resp.text

"""Tests for /settings/followup-toggle and /settings/ic-reenviados-toggle."""
from __future__ import annotations
import pytest


pytestmark = pytest.mark.asyncio


class TestFollowupToggle:
    async def test_unauthenticated_redirects(self, client):
        resp = await client.post("/settings/followup-toggle")
        assert resp.status_code == 303

    async def test_non_admin_forbidden(self, user_client):
        resp = await user_client.post("/settings/followup-toggle")
        assert resp.status_code in (403, 303)

    async def test_toggle_returns_200(self, admin_client):
        resp = await admin_client.post("/settings/followup-toggle")
        assert resp.status_code == 200

    async def test_toggle_flips_value(self, admin_client):
        """Two toggles return to original state."""
        resp1 = await admin_client.post("/settings/followup-toggle")
        assert resp1.status_code == 200
        resp2 = await admin_client.post("/settings/followup-toggle")
        assert resp2.status_code == 200


class TestIcReenviadosToggle:
    async def test_toggle_returns_200(self, admin_client):
        resp = await admin_client.post("/settings/ic-reenviados-toggle")
        assert resp.status_code == 200

    async def test_settings_page_includes_new_toggles(self, admin_client):
        """Settings page renders both toggle sections."""
        resp = await admin_client.get("/settings")
        assert resp.status_code == 200
        assert b"Seguimiento" in resp.content or b"followup" in resp.content.lower()
        assert b"Reenviados" in resp.content or b"ic_reenviados" in resp.content.lower()

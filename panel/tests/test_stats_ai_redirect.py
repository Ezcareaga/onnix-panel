"""Tests for GET /stats/ai — 301 redirect to unified /stats/health page.

The old /stats/ai endpoint now permanently redirects to /stats/health?tab=detalle
for backwards compatibility.  No DB queries are involved.
"""
from __future__ import annotations

import pytest


class TestStatsAiRedirect:

    @pytest.mark.asyncio
    async def test_stats_ai_redirects_to_health(self, admin_client):
        """GET /stats/ai must return 301 with Location pointing to unified page."""
        resp = await admin_client.get("/stats/ai", follow_redirects=False)
        assert resp.status_code == 301
        location = resp.headers.get("location", "")
        assert "/stats/health" in location
        assert "tab=detalle" in location

    @pytest.mark.asyncio
    async def test_stats_ai_redirect_unauthenticated(self, client):
        """The redirect itself is auth-free — 301 regardless of auth state."""
        resp = await client.get("/stats/ai", follow_redirects=False)
        assert resp.status_code == 301
        assert "/stats/health" in resp.headers.get("location", "")

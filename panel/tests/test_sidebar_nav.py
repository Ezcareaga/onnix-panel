"""Sidebar navigation tests — verify visible nav links on every page."""
import pytest


class TestSidebarLinks:
    async def test_dashboard_includes_propiedades_link(self, admin_client):
        resp = await admin_client.get("/dashboard")
        assert resp.status_code == 200
        body = resp.text
        assert 'href="/properties"' in body
        assert "Propiedades" in body

    async def test_propiedades_link_is_active_on_properties_route(self, admin_client):
        resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        body = resp.text
        # nav-active class is applied to current section
        assert 'href="/properties"' in body
        assert "nav-active" in body

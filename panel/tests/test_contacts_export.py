"""Tests for GET /contacts/export (C1.4).

Covers:
  - Auth: unauthenticated → 303; authenticated → 200 (all roles allowed)
  - Response headers: content-type text/csv, content-disposition attachment .csv
  - CSV content: BOM present, correct headers, data rows
  - Filter pass-through: status, source, search, phone are forwarded to service
  - Agent authz: agent_user_id implicitly set from user.id
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest


# Service module target for patching
_EXPORT_TARGET = "app.routes.contacts.contact_service.export_csv"

CSV_FILENAME_DATE = "contactos_"  # prefix of the filename


def _make_export_result(rows: int = 1) -> tuple[str, str]:
    """Return a minimal (csv_str, filename) tuple for the mock."""
    lines = ["id,nombre,telefono,email,estado,fuente,asesor_asignado,creado,ultima_actividad"]
    for i in range(rows):
        lines.append(f"{i+1},Test{i+1},+595981000{i:03d},,new,manual,,,")
    csv_str = "﻿" + "\r\n".join(lines)  # BOM prefix
    return csv_str, f"contactos_20260612.csv"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestExportAuth:
    async def test_unauthenticated_redirects_to_login(self, client):
        resp = await client.get("/contacts/export")
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    async def test_admin_gets_200(self, admin_client):
        with patch(_EXPORT_TARGET, new=AsyncMock(return_value=_make_export_result())):
            resp = await admin_client.get("/contacts/export")
        assert resp.status_code == 200

    async def test_user_role_gets_200(self, user_client):
        """role='user' (legacy) has full list access → export also allowed."""
        with patch(_EXPORT_TARGET, new=AsyncMock(return_value=_make_export_result())):
            resp = await user_client.get("/contacts/export")
        assert resp.status_code == 200

    async def test_agent_gets_200(self, agent_client):
        with patch(_EXPORT_TARGET, new=AsyncMock(return_value=_make_export_result())):
            resp = await agent_client.get("/contacts/export")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------

class TestExportResponse:
    async def test_content_type_is_csv(self, admin_client):
        with patch(_EXPORT_TARGET, new=AsyncMock(return_value=_make_export_result())):
            resp = await admin_client.get("/contacts/export")
        assert "text/csv" in resp.headers["content-type"]

    async def test_content_disposition_is_attachment_csv(self, admin_client):
        with patch(_EXPORT_TARGET, new=AsyncMock(return_value=_make_export_result())):
            resp = await admin_client.get("/contacts/export")
        disposition = resp.headers["content-disposition"]
        assert "attachment" in disposition
        assert ".csv" in disposition

    async def test_filename_contains_date_prefix(self, admin_client):
        with patch(_EXPORT_TARGET, new=AsyncMock(return_value=_make_export_result())):
            resp = await admin_client.get("/contacts/export")
        disposition = resp.headers["content-disposition"]
        assert CSV_FILENAME_DATE in disposition

    async def test_body_starts_with_bom(self, admin_client):
        """UTF-8 BOM (EF BB BF) must be present for Excel compatibility."""
        with patch(_EXPORT_TARGET, new=AsyncMock(return_value=_make_export_result())):
            resp = await admin_client.get("/contacts/export")
        assert resp.content[:3] == b"\xef\xbb\xbf"

    async def test_body_has_header_row(self, admin_client):
        with patch(_EXPORT_TARGET, new=AsyncMock(return_value=_make_export_result())):
            resp = await admin_client.get("/contacts/export")
        text = resp.content.decode("utf-8-sig")  # strip BOM
        first_line = text.splitlines()[0]
        assert "nombre" in first_line
        assert "telefono" in first_line
        assert "estado" in first_line


# ---------------------------------------------------------------------------
# Filter pass-through
# ---------------------------------------------------------------------------

class TestExportFilters:
    async def test_status_filter_forwarded(self, admin_client):
        mock_export = AsyncMock(return_value=_make_export_result(0))
        with patch(_EXPORT_TARGET, new=mock_export):
            resp = await admin_client.get("/contacts/export?status=interested")
        assert resp.status_code == 200
        _, kwargs = mock_export.call_args
        assert kwargs["status"] == "interested"

    async def test_source_filter_forwarded(self, admin_client):
        mock_export = AsyncMock(return_value=_make_export_result(0))
        with patch(_EXPORT_TARGET, new=mock_export):
            resp = await admin_client.get("/contacts/export?source=whatsapp")
        assert resp.status_code == 200
        _, kwargs = mock_export.call_args
        assert kwargs["source"] == "whatsapp"

    async def test_search_filter_forwarded(self, admin_client):
        mock_export = AsyncMock(return_value=_make_export_result(0))
        with patch(_EXPORT_TARGET, new=mock_export):
            resp = await admin_client.get("/contacts/export?search=pedro")
        assert resp.status_code == 200
        _, kwargs = mock_export.call_args
        assert kwargs["search"] == "pedro"

    async def test_phone_filter_forwarded(self, admin_client):
        mock_export = AsyncMock(return_value=_make_export_result(0))
        with patch(_EXPORT_TARGET, new=mock_export):
            resp = await admin_client.get("/contacts/export?phone=with")
        assert resp.status_code == 200
        _, kwargs = mock_export.call_args
        assert kwargs["phone_filter"] == "with"

    async def test_invalid_phone_filter_normalised_to_none(self, admin_client):
        mock_export = AsyncMock(return_value=_make_export_result(0))
        with patch(_EXPORT_TARGET, new=mock_export):
            resp = await admin_client.get("/contacts/export?phone=garbage")
        assert resp.status_code == 200
        _, kwargs = mock_export.call_args
        assert kwargs["phone_filter"] is None


# ---------------------------------------------------------------------------
# Agent authz — agent_user_id is set implicitly from user.id
# ---------------------------------------------------------------------------

class TestExportAgentAuthz:
    async def test_agent_receives_own_user_id_as_filter(self, agent_client):
        """Agent's export must pass agent_user_id=user.id so they only see
        their own contacts, matching the list view behaviour."""
        mock_export = AsyncMock(return_value=_make_export_result(0))
        with patch(_EXPORT_TARGET, new=mock_export):
            resp = await agent_client.get("/contacts/export")
        assert resp.status_code == 200
        _, kwargs = mock_export.call_args
        # agent_user_id must be an int (the agent's own id), not None
        assert kwargs["agent_user_id"] is not None
        assert isinstance(kwargs["agent_user_id"], int)

    async def test_admin_receives_none_as_agent_filter(self, admin_client):
        """Admin sees all contacts — agent_user_id must be None."""
        mock_export = AsyncMock(return_value=_make_export_result(0))
        with patch(_EXPORT_TARGET, new=mock_export):
            resp = await admin_client.get("/contacts/export")
        assert resp.status_code == 200
        _, kwargs = mock_export.call_args
        assert kwargs["agent_user_id"] is None

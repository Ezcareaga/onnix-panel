"""TDD — property_service.py

Pure unit tests: no DB connection required.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.property_service import (
    PropertyFilters,
    PropertyService,
    _compute_public_path,
    attach_public_paths,
    calc_estimated_expiry,
)


class TestPropertyFiltersDefaults:
    def test_filters_dataclass_defaults(self):
        f = PropertyFilters()
        assert f.property_type is None
        assert f.operation is None
        assert f.city is None
        assert f.neighborhood is None
        assert f.price_min is None
        assert f.price_max is None
        assert f.currency is None
        assert f.bedrooms_min is None
        assert f.state == "active"
        assert f.source is None
        assert f.construction_state is None
        assert f.updated_within_days is None
        assert f.search_text is None


class TestCalcEstimatedExpiry:
    def test_calc_estimated_expiry_uses_portal_when_present(self):
        created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        portal_expires_at = datetime(2025, 6, 15, tzinfo=timezone.utc)
        date, is_real = calc_estimated_expiry(created_at, portal_expires_at)
        assert date == portal_expires_at
        assert is_real is True

    def test_calc_estimated_expiry_falls_back_to_created_plus_180(self):
        created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        date, is_real = calc_estimated_expiry(created_at, None)
        assert date == created_at + timedelta(days=180)
        assert is_real is False


class TestGetProperties:
    async def test_get_properties_calls_repo_with_offset(self):
        db = AsyncMock()
        filters = PropertyFilters()

        with patch("app.services.property_service.property_repo") as mock_repo:
            mock_repo.list_with_filters = AsyncMock(return_value=[])
            mock_repo.count_with_filters = AsyncMock(return_value=0)

            rows, total = await PropertyService.get_properties(db, filters, page=3, per_page=20)

            mock_repo.list_with_filters.assert_called_once_with(
                db, filters, limit=20, offset=40
            )
            mock_repo.count_with_filters.assert_called_once_with(db, filters)
            assert rows == []
            assert total == 0


def _list_row(**over) -> dict:
    """Minimal listing row (M6.5 public_path) — eligible by default."""
    base = {
        "id": 42,
        "source": "remax",
        "is_active": True,
        "on_hold": False,
        "title": "Casa en Asuncion",
        "city": "Asuncion",
    }
    base.update(over)
    return base


def _detail_row(**over) -> dict:
    """Minimal get_full_detail row — eligible by default."""
    base = _list_row()
    base.update({"external_id": "RX-1", "local_image_count": 0})
    base.update(over)
    return base


class TestComputePublicPath:
    """M6.5 — public_path por fila: solo elegibles M6.4 tienen link público."""

    async def test_public_path_only_for_eligible_rows(self):
        rows = [
            _list_row(id=1),                              # remax activa no-hold
            _list_row(id=2, source="infocasas"),          # infocasas activa
            _list_row(id=3, is_active=False),             # remax inactiva
            _list_row(id=4, on_hold=True),                # remax on_hold
        ]
        db = AsyncMock()
        with patch("app.services.property_service.property_repo") as mock_repo:
            mock_repo.list_with_filters = AsyncMock(return_value=rows)
            mock_repo.count_with_filters = AsyncMock(return_value=4)
            out, total = await PropertyService.get_properties(db, PropertyFilters())

        assert total == 4
        assert out[0]["public_path"] == "/prop/1-casa-en-asuncion-asuncion"
        assert out[1]["public_path"] is None
        assert out[2]["public_path"] is None
        assert out[3]["public_path"] is None

    def test_copy_public_link_format(self):
        # Acentos y enie van por la slugify REAL de M6.4 (app.utils.slug)
        row = _list_row(id=7, title="Casa en Ñemby único", city=None)
        assert _compute_public_path(row) == "/prop/7-casa-en-nemby-unico"

        # Mismo manejo que el público: title None → fallback de slugify
        row = _list_row(id=8, title=None, city=None)
        assert _compute_public_path(row) == "/prop/8-propiedad"

    def test_public_whitelist_imported_not_duplicated(self, monkeypatch):
        from app.services.public_property_service import PublicPropertyService

        row = _list_row(id=9, source="infocasas", title="Casa", city=None)
        assert _compute_public_path(row) is None

        # Mutar la whitelist DEL MODULO PUBLICO cambia el comportamiento de
        # property_service → la whitelist usada ES ese objeto, no una copia.
        monkeypatch.setattr(
            PublicPropertyService, "PUBLIC_SOURCES", ("infocasas",)
        )
        assert _compute_public_path(row) == "/prop/9-casa"

    def test_attach_public_paths_mutates_each_row(self):
        rows = [_list_row(id=1), _list_row(id=2, source="infocasas")]
        out = attach_public_paths(rows)
        assert out is rows
        assert out[0]["public_path"] == "/prop/1-casa-en-asuncion-asuncion"
        assert out[1]["public_path"] is None


class TestDetailIncludesPublicPath:
    async def test_detail_includes_public_path(self):
        db = AsyncMock()
        with patch("app.services.property_service.property_repo") as mock_repo:
            mock_repo.get_full_detail = AsyncMock(return_value=_detail_row())
            detail = await PropertyService.get_property_detail(db, 42)
        assert detail["public_path"] == "/prop/42-casa-en-asuncion-asuncion"

        with patch("app.services.property_service.property_repo") as mock_repo:
            mock_repo.get_full_detail = AsyncMock(
                return_value=_detail_row(source="infocasas")
            )
            detail = await PropertyService.get_property_detail(db, 42)
        assert detail["public_path"] is None

"""TDD — PublicPropertyService (M6.4 Vista Publica)

Covers:
- get_public_detail: eligible, ineligible (source, inactive, on_hold, missing)
- photo_urls empty when local_image_count=0 (no fallback to main_image_url)
- get_full_detail includes latitude/longitude (repo schema smoke)
- get_sitemap_entries: includes eligible, excludes IC/inactive/on_hold
- price_display: USD, PYG-only, neither
- public_code zero-padding
- wa_url encoding

All property_service / property_repo calls are mocked — no DB required.
The repo schema test (latitude/longitude in SELECT) uses db fixture (real DB).
"""
from __future__ import annotations

import urllib.parse
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from app.repositories.property_repo import PropertyRepository
from app.services.public_property_service import PublicPropertyService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    *,
    prop_id: int = 1,
    source: str = "remax",
    title: str = "Casa en Venta",
    city: str = "Asuncion",
    is_active: bool = True,
    on_hold: bool = False,
    price_usd=None,
    price_pyg=None,
    local_image_count: int = 3,
    external_id: str = "EXT001",
    updated_at=None,
) -> dict:
    """Build a minimal property row dict as returned by property_service."""
    if updated_at is None:
        updated_at = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    count = local_image_count
    photo_urls = [
        f"/images/{source}/{external_id}/{i}.webp"
        for i in range(1, min(count, 15) + 1)
    ]
    return {
        "id": prop_id,
        "source": source,
        "external_id": external_id,
        "title": title,
        "city": city,
        "is_active": is_active,
        "on_hold": on_hold,
        "price_usd": price_usd,
        "price_pyg": price_pyg,
        "local_image_count": local_image_count,
        "main_image_url": "https://portal.com/original.jpg",  # must NOT be used
        "photo_urls": photo_urls,
        "updated_at": updated_at,
    }


def _patch_property_service(return_value):
    """Patch property_service.get_property_detail inside the public service module."""
    return patch(
        "app.services.public_property_service.property_service.get_property_detail",
        new=AsyncMock(return_value=return_value),
    )


def _patch_property_repo_sitemap(return_value):
    """Patch property_repo.get_public_sitemap_rows inside the public service module."""
    return patch(
        "app.services.public_property_service.property_repo.get_public_sitemap_rows",
        new=AsyncMock(return_value=return_value),
    )


# ---------------------------------------------------------------------------
# TestIsPublicEligible
# ---------------------------------------------------------------------------


class TestIsPublicEligible:
    def test_remax_active_not_on_hold_is_eligible(self):
        row = _make_row(source="remax", is_active=True, on_hold=False)
        assert PublicPropertyService.is_public_eligible(row) is True

    def test_onnixpy_active_not_on_hold_is_eligible(self):
        row = _make_row(source="onnixpy", is_active=True, on_hold=False)
        assert PublicPropertyService.is_public_eligible(row) is True

    def test_coldwell_active_is_eligible(self):
        row = _make_row(source="coldwell", is_active=True, on_hold=False)
        assert PublicPropertyService.is_public_eligible(row) is True

    def test_psir_active_is_eligible(self):
        row = _make_row(source="psir", is_active=True, on_hold=False)
        assert PublicPropertyService.is_public_eligible(row) is True

    def test_infocasas_is_not_eligible(self):
        row = _make_row(source="infocasas", is_active=True, on_hold=False)
        assert PublicPropertyService.is_public_eligible(row) is False

    def test_inactive_is_not_eligible(self):
        row = _make_row(source="remax", is_active=False, on_hold=False)
        assert PublicPropertyService.is_public_eligible(row) is False

    def test_on_hold_is_not_eligible(self):
        row = _make_row(source="remax", is_active=True, on_hold=True)
        assert PublicPropertyService.is_public_eligible(row) is False

    def test_is_active_none_is_not_eligible(self):
        row = _make_row(source="remax", is_active=True, on_hold=False)
        row["is_active"] = None
        assert PublicPropertyService.is_public_eligible(row) is False


# ---------------------------------------------------------------------------
# TestGetPublicDetail — eligible case
# ---------------------------------------------------------------------------


class TestGetPublicDetailEligible:
    async def test_returns_dict_with_slug(self):
        row = _make_row(
            prop_id=42,
            source="remax",
            title="Casa en Venta",
            city="Asuncion",
            is_active=True,
            on_hold=False,
            price_usd=150000,
        )
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, 42)

        assert result is not None
        assert "slug" in result
        assert result["slug"] == "casa-en-venta-asuncion"

    async def test_canonical_path_format(self):
        row = _make_row(prop_id=42, source="remax", title="Casa Asuncion", city="Luque")
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, 42)

        assert result["canonical_path"] == f"/prop/42-{result['slug']}"

    async def test_public_code_zero_padded_5_digits(self):
        row = _make_row(prop_id=7, source="remax", is_active=True)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, 7)

        assert result["public_code"] == "00007"

    async def test_public_code_large_id(self):
        row = _make_row(prop_id=12345, source="remax", is_active=True)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, 12345)

        assert result["public_code"] == "12345"

    async def test_wa_url_contains_encoded_message_with_code(self):
        prop_id = 42
        row = _make_row(prop_id=prop_id, source="remax", is_active=True)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, prop_id)

        expected_code = "00042"
        expected_message = f"Hola! Me interesa la propiedad {expected_code} que vi en onnix.com.py"
        expected_encoded = urllib.parse.quote(expected_message)
        assert f"text={expected_encoded}" in result["wa_url"]

    async def test_wa_url_has_correct_number(self):
        row = _make_row(prop_id=1, source="remax", is_active=True)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, 1)

        assert "wa.me/595900000000" in result["wa_url"]

    async def test_wa_url_fotos_pregunta_por_las_fotos_y_nombra_la_propiedad(self):
        """La ficha sin foto convierte la ausencia en una acción, y esa acción
        tiene que llegar diciendo de qué se trata: con el `wa_url` genérico el
        asesor recibe «me interesa la propiedad 00042» y no sabe que le están
        pidiendo las fotos. Son 3.518 fichas (17,6% del catálogo activo, medido
        el 2026-08-23), casi todas alcanzadas por sitemap o por link de asesor.
        """
        row = _make_row(prop_id=42, source="remax", is_active=True)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, 42)

        esperado = urllib.parse.quote("Hola! ¿Me pasás las fotos de la propiedad 00042?")
        assert f"text={esperado}" in result["wa_url_fotos"]
        assert "wa.me/595900000000" in result["wa_url_fotos"]
        assert result["wa_url_fotos"] != result["wa_url"]

    async def test_wa_url_datos_pregunta_por_los_datos(self):
        """El otro vacío de la ficha: 104 fichas quedaban con la etiqueta
        «Características» sobre un div vacío. El enlace que la reemplaza tiene
        que diferenciarse del CTA de la página, que queda a 200px."""
        row = _make_row(prop_id=42, source="remax", is_active=True)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, 42)

        esperado = urllib.parse.quote("Hola! ¿Me pasás los datos de la propiedad 00042?")
        assert f"text={esperado}" in result["wa_url_datos"]
        assert result["wa_url_datos"] != result["wa_url"]
        assert result["wa_url_datos"] != result["wa_url_fotos"]

    async def test_los_dos_links_salen_del_mismo_armador(self):
        """`wa_link` es el único armador del portal. Existía dos veces —acá y
        adentro de `routes/public.py`, con su propio import de urllib— y en este
        repo lo escrito dos veces ya divergió cuatro veces en el panel."""
        from app.services.public_property_service import wa_link

        assert wa_link("595900000000", "hola qué tal") == (
            "https://wa.me/595900000000?text=hola%20qu%C3%A9%20tal"
        )

    async def test_photo_urls_preserved_from_service(self):
        """photo_urls from property_service must be passed through unchanged."""
        row = _make_row(prop_id=1, source="remax", external_id="EXT1", local_image_count=3)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, 1)

        assert result["photo_urls"] == [
            "/images/remax/EXT1/1.webp",
            "/images/remax/EXT1/2.webp",
            "/images/remax/EXT1/3.webp",
        ]


# ---------------------------------------------------------------------------
# TestGetPublicDetailIneligible
# ---------------------------------------------------------------------------


class TestGetPublicDetailIneligible:
    async def test_infocasas_source_returns_none(self):
        row = _make_row(source="infocasas", is_active=True, on_hold=False)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert result is None

    async def test_inactive_returns_none(self):
        row = _make_row(source="remax", is_active=False, on_hold=False)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert result is None

    async def test_on_hold_returns_none(self):
        row = _make_row(source="remax", is_active=True, on_hold=True)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert result is None

    async def test_nonexistent_property_returns_none(self):
        db = AsyncMock()
        with _patch_property_service(None):
            result = await PublicPropertyService.get_public_detail(db, 999999)
        assert result is None


# ---------------------------------------------------------------------------
# TestPhotoUrlsNoFallback
# ---------------------------------------------------------------------------


class TestPhotoUrlsNoFallback:
    async def test_empty_photo_urls_when_local_image_count_zero(self):
        """When local_image_count=0 property_service returns [] for photo_urls.

        public_property_service must NOT fall back to main_image_url.
        """
        row = _make_row(
            source="remax",
            is_active=True,
            local_image_count=0,
        )
        # Manually set photo_urls=[] as property_service would when count=0
        row["photo_urls"] = []

        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])

        assert result is not None
        assert result["photo_urls"] == []
        # main_image_url is stripped by the LOW-1 defense-in-depth fix and
        # must NOT appear in the returned dict at all.
        assert "main_image_url" not in result


# ---------------------------------------------------------------------------
# TestPriceDisplay
# ---------------------------------------------------------------------------


class TestPriceDisplay:
    async def test_price_display_usd(self):
        row = _make_row(source="remax", is_active=True, price_usd=150000)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert result["price_display"] == "USD 150.000"

    async def test_price_display_usd_large(self):
        row = _make_row(source="remax", is_active=True, price_usd=1200000)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert result["price_display"] == "USD 1.200.000"

    async def test_price_display_pyg_only(self):
        row = _make_row(source="remax", is_active=True, price_usd=None, price_pyg=1200000000)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert result["price_display"] == "₲ 1.200.000.000"

    async def test_price_display_outlier_venta_masked(self):
        """M6.4b — implausible source price masked as 'Consultar precio' on public detail."""
        row = _make_row(source="remax", is_active=True, price_usd=300_000_000, price_pyg=1)
        row["operation"] = "venta"
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert result["price_display"] == "Consultar precio"

    async def test_price_display_no_price(self):
        row = _make_row(source="remax", is_active=True, price_usd=None, price_pyg=None)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert result["price_display"] == "Consultar precio"


# ---------------------------------------------------------------------------
# TestPrivateFieldsStripped — LOW-1 (defense in depth)
# ---------------------------------------------------------------------------


class TestPrivateFieldsStripped:
    """get_public_detail must strip private fields before returning the dict.

    Fields that must NEVER reach the public view:
    url, agent_name, agent_phone, agent_whatsapp, main_image_url
    """

    _PRIVATE_FIELDS = ("url", "agent_name", "agent_phone", "agent_whatsapp", "main_image_url")

    def _make_row_with_private_fields(self) -> dict:
        row = _make_row(source="remax", is_active=True, price_usd=100000)
        row["url"] = "https://remax.com.py/prop/123"
        row["agent_name"] = "Juan Perez"
        row["agent_phone"] = "+595981000001"
        row["agent_whatsapp"] = "+595981000001"
        # main_image_url already set by _make_row
        return row

    async def test_url_not_in_result(self):
        row = self._make_row_with_private_fields()
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert "url" not in result

    async def test_agent_name_not_in_result(self):
        row = self._make_row_with_private_fields()
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert "agent_name" not in result

    async def test_agent_phone_not_in_result(self):
        row = self._make_row_with_private_fields()
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert "agent_phone" not in result

    async def test_agent_whatsapp_not_in_result(self):
        row = self._make_row_with_private_fields()
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert "agent_whatsapp" not in result

    async def test_main_image_url_not_in_result(self):
        row = self._make_row_with_private_fields()
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert "main_image_url" not in result

    async def test_all_five_private_fields_absent(self):
        """Single assertion covering all 5 forbidden keys at once."""
        row = self._make_row_with_private_fields()
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        present = [f for f in self._PRIVATE_FIELDS if f in result]
        assert present == [], f"Private fields leaked into public detail: {present}"


# ---------------------------------------------------------------------------
# TestPriceDisplayTruthiness — LOW-2 (price_usd=0 must not show "USD 0")
# ---------------------------------------------------------------------------


class TestPriceDisplayTruthiness:
    """price_usd=0 is falsy and must fall through to PYG or 'Consultar precio'."""

    async def test_price_usd_zero_with_valid_pyg_shows_pyg(self):
        row = _make_row(source="remax", is_active=True, price_usd=0, price_pyg=500000000)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert result["price_display"] == "₲ 500.000.000"

    async def test_price_usd_zero_and_price_pyg_zero_shows_consultar(self):
        row = _make_row(source="remax", is_active=True, price_usd=0, price_pyg=0)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert result["price_display"] == "Consultar precio"

    async def test_price_usd_zero_and_price_pyg_none_shows_consultar(self):
        row = _make_row(source="remax", is_active=True, price_usd=0, price_pyg=None)
        db = AsyncMock()
        with _patch_property_service(row):
            result = await PublicPropertyService.get_public_detail(db, row["id"])
        assert result["price_display"] == "Consultar precio"


# ---------------------------------------------------------------------------
# TestGetSitemapEntries
# ---------------------------------------------------------------------------


class TestGetSitemapEntries:
    def _make_sitemap_row(
        self,
        *,
        prop_id: int,
        title: str = "Casa",
        city: str = "Asuncion",
        updated_at=None,
    ) -> dict:
        if updated_at is None:
            updated_at = datetime(2025, 3, 15, tzinfo=timezone.utc)
        return {"id": prop_id, "title": title, "city": city, "updated_at": updated_at}

    async def test_eligible_row_produces_entry(self):
        rows = [self._make_sitemap_row(prop_id=10, title="Casa", city="Luque")]
        db = AsyncMock()
        with _patch_property_repo_sitemap(rows):
            entries = await PublicPropertyService.get_sitemap_entries(db)

        assert len(entries) == 1
        assert entries[0]["loc"].startswith("/prop/10-")
        assert entries[0]["lastmod"] == "2025-03-15"

    async def test_lastmod_is_iso_date(self):
        rows = [
            self._make_sitemap_row(
                prop_id=1,
                updated_at=datetime(2024, 11, 30, 8, 30, tzinfo=timezone.utc),
            )
        ]
        db = AsyncMock()
        with _patch_property_repo_sitemap(rows):
            entries = await PublicPropertyService.get_sitemap_entries(db)

        assert entries[0]["lastmod"] == "2024-11-30"

    async def test_null_updated_at_produces_none_lastmod(self):
        row = self._make_sitemap_row(prop_id=5)
        row["updated_at"] = None
        db = AsyncMock()
        with _patch_property_repo_sitemap([row]):
            entries = await PublicPropertyService.get_sitemap_entries(db)

        assert entries[0]["lastmod"] is None

    async def test_empty_repo_returns_empty_list(self):
        db = AsyncMock()
        with _patch_property_repo_sitemap([]):
            entries = await PublicPropertyService.get_sitemap_entries(db)
        assert entries == []

    async def test_repo_called_with_public_sources(self):
        """Repo must receive exactly the PUBLIC_SOURCES tuple."""
        db = AsyncMock()
        mock_repo = AsyncMock(return_value=[])
        with patch(
            "app.services.public_property_service.property_repo.get_public_sitemap_rows",
            new=mock_repo,
        ):
            await PublicPropertyService.get_sitemap_entries(db)

        mock_repo.assert_awaited_once()
        _, kwargs = mock_repo.call_args
        args = mock_repo.call_args[0]
        # Second positional arg is sources
        passed_sources = args[1] if len(args) > 1 else kwargs.get("sources")
        for src in PublicPropertyService.PUBLIC_SOURCES:
            assert src in passed_sources


# ---------------------------------------------------------------------------
# TestGetPublicSitemapRowsSQL — unit: SQL inspection (mocked db)
# ---------------------------------------------------------------------------


class TestGetPublicSitemapRowsSQL:
    def _make_db(self, rows=None) -> AsyncMock:
        db = AsyncMock()
        mock_result = MagicMock()
        mock_mappings = MagicMock()
        mock_mappings.all.return_value = rows or []
        mock_result.mappings.return_value = mock_mappings
        db.execute = AsyncMock(return_value=mock_result)
        return db

    def _executed_sql(self, db) -> str:
        args, _ = db.execute.call_args
        return str(args[0])

    async def test_sql_filters_is_active_true(self):
        db = self._make_db()
        await PropertyRepository.get_public_sitemap_rows(db, ["remax"])
        sql = self._executed_sql(db)
        assert "is_active" in sql.lower()

    async def test_sql_filters_on_hold(self):
        db = self._make_db()
        await PropertyRepository.get_public_sitemap_rows(db, ["remax"])
        sql = self._executed_sql(db)
        assert "on_hold" in sql.lower()

    async def test_sql_filters_source(self):
        db = self._make_db()
        await PropertyRepository.get_public_sitemap_rows(db, ["remax", "coldwell"])
        sql = self._executed_sql(db)
        assert "source" in sql.lower()

    async def test_empty_sources_short_circuits(self):
        db = self._make_db()
        result = await PropertyRepository.get_public_sitemap_rows(db, [])
        assert result == []
        db.execute.assert_not_called()

    async def test_returns_list_of_dicts(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_mappings = MagicMock()
        mock_mappings.all.return_value = [{"id": 1, "title": "T", "city": "C", "updated_at": None}]
        mock_result.mappings.return_value = mock_mappings
        db.execute = AsyncMock(return_value=mock_result)
        result = await PropertyRepository.get_public_sitemap_rows(db, ["remax"])
        assert isinstance(result, list)
        assert result[0]["id"] == 1


# ---------------------------------------------------------------------------
# TestGetFullDetailLatLon — real DB: schema smoke (latitude/longitude present)
# ---------------------------------------------------------------------------


class TestGetFullDetailLatLon:
    async def test_get_full_detail_includes_latitude_longitude(self, db):
        """get_full_detail must include latitude and longitude in the SELECT.

        Hits the dev DB to catch column-name drift. Skips if no active row
        exists (should never happen on the staging snapshot).
        """
        row = (
            await db.execute(
                text("SELECT MIN(id) AS id FROM properties WHERE is_active = true")
            )
        ).mappings().one()
        property_id = row["id"]
        if property_id is None:
            pytest.skip("dev DB has no active properties — skipping schema smoke")

        result = await PropertyRepository.get_full_detail(db, property_id)

        assert result is not None
        assert "latitude" in result, "latitude column missing from get_full_detail SELECT"
        assert "longitude" in result, "longitude column missing from get_full_detail SELECT"

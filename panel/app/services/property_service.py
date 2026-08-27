from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.property_repo import property_repo
from app.utils.fotos import urls_fotos
from app.utils.slug import slugify


@dataclass
class PropertyFilters:
    property_type: str | None = None
    operation: str | None = None
    city: str | None = None
    neighborhood: str | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    # Elige contra qué columna se compara el rango (price_usd / price_pyg).
    # NO filtra por la etiqueta `price_currency` de la fila.
    currency: str | None = None
    bedrooms_min: int | None = None
    bathrooms_min: int | None = None
    state: str | None = "active"
    source: str | None = None
    construction_state: str | None = None
    updated_within_days: int | None = None
    search_text: str | None = None
    amenities: list[str] | None = None
    barato: bool = False


def calc_estimated_expiry(
    created_at: datetime,
    portal_expires_at: datetime | None,
) -> tuple[datetime, bool]:
    """Return (expiry_date, is_real).

    is_real=True when portal gave us an explicit date; False when we
    estimate 180 days from created_at.
    """
    if portal_expires_at is not None:
        return portal_expires_at, True
    return created_at + timedelta(days=180), False


_PHOTO_CAP = 15


def _compute_public_path(row: dict) -> str | None:
    """Return the public URL path (``/prop/{id}-{slug}``) or None.

    M6.5: el panel lista TODAS las sources/estados, pero el link público
    (M6.4) solo existe para filas elegibles. Reusa la elegibilidad
    (``is_public_eligible`` → whitelist PUBLIC_SOURCES + is_active +
    not on_hold) y la construcción de slug del módulo público —
    ``slugify(f"{title} {city}")`` con fallback "propiedad" en slugify.

    El import es lazy porque ``public_property_service`` importa
    ``property_service`` a nivel de módulo (evita import circular).
    """
    from app.services.public_property_service import PublicPropertyService

    if not PublicPropertyService.is_public_eligible(row):
        return None
    title = row.get("title") or ""
    city = row.get("city") or ""
    slug = slugify(f"{title} {city}".strip())
    return f"/prop/{row['id']}-{slug}"


def attach_public_paths(rows: list[dict]) -> list[dict]:
    """Set ``public_path`` on every listing row (None = sin link público).

    Helper compartido: lo usan ``get_properties`` (listado clásico) y
    ``panel_hybrid_search.search`` (filas hidratadas con list_by_ids).
    """
    for row in rows:
        row["public_path"] = _compute_public_path(row)
    return rows


class PropertyService:
    @staticmethod
    async def set_active(db: AsyncSession, property_id: int, value: bool) -> bool:
        """Toggle is_active for a property. Returns the new value when updated."""
        return await property_repo.set_active(db, property_id, value)

    @staticmethod
    async def get_property_detail(db: AsyncSession, property_id: int) -> dict | None:
        """Return full property detail dict with computed photo_urls list, or None."""
        row = await property_repo.get_full_detail(db, property_id)
        if row is None:
            return None
        count = min(row.get("local_image_count") or 0, _PHOTO_CAP)
        source = row["source"] or ""
        ext_id = row["external_id"] or ""
        row["photo_urls"] = urls_fotos(source, ext_id, count)
        row["public_path"] = _compute_public_path(row)
        return row

    @staticmethod
    async def get_properties(
        db: AsyncSession,
        filters: PropertyFilters,
        page: int = 1,
        per_page: int = 50,
    ) -> tuple[list[dict], int]:
        """Return (rows, total_count) for the given filters and page."""
        offset = (page - 1) * per_page
        rows, total = await _fetch_parallel(db, filters, per_page, offset)
        return attach_public_paths(rows), total

    @staticmethod
    async def get_state_counts(db: AsyncSession) -> dict:
        """Return total counts for active / on_hold / inactive (unfiltered)."""
        return await property_repo.count_by_state(db)

    @staticmethod
    async def get_empty_hints(
        db: AsyncSession,
        filters: PropertyFilters,
    ) -> list[dict] | None:
        """Suggest which filter to relax when a search returns zero rows.

        For each "relaxable" field set in ``filters`` (property_type, city,
        neighborhood) we re-run the count with that single field cleared and
        report how many properties WOULD match. The list is shown inline
        below the empty state so la administradora can fix the search in one click.

        Returns None when:
          - No relaxable field is set (only state=active default).
          - Every relaxation also yields zero (no useful suggestion).
        """
        relaxable = ["property_type", "city", "neighborhood"]
        active = [f for f in relaxable if getattr(filters, f, None)]
        if not active:
            return None

        hints: list[dict] = []
        for field_name in active:
            relaxed = replace(filters, **{field_name: None})
            count = await property_repo.count_with_filters(db, relaxed)
            if count > 0:
                hints.append(
                    {
                        "drop": field_name,
                        "count": count,
                        "dropped_value": getattr(filters, field_name),
                    }
                )

        return hints or None


async def _fetch_parallel(
    db: AsyncSession,
    filters: PropertyFilters,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    rows = await property_repo.list_with_filters(db, filters, limit=limit, offset=offset)
    total = await property_repo.count_with_filters(db, filters)
    return rows, total


property_service = PropertyService()

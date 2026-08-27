"""TDD — empty hints para /properties cuando no hay resultados.

Cuando una busqueda devuelve 0 propiedades y al menos un filtro de tipo o
ubicacion esta activo, el panel sugiere relajar uno de esos filtros usando
conteos reales de la DB. Asi la administradora ve si la propiedad "no existe" o si
solo necesita ampliar el rango.

property_service.get_empty_hints(db, filters) -> list[dict] | None
- Devuelve None cuando no hay un filtro relajable activo (no aporta).
- Devuelve una lista de dicts con {drop, label, count, redirect_qs} para
  cada filtro relajable. count se calcula corriendo el filtro con ese
  campo en None.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.property_service import (
    PropertyFilters,
    property_service,
)


def _make_db():
    """Devuelve un AsyncSession mockeado donde scalar() devuelve valores
    encolados — uno por cada count_with_filters."""
    db = AsyncMock()
    return db


def _queue_counts(*values):
    """Helper: encadena las respuestas de db.execute para count_with_filters."""
    results = []
    for v in values:
        result = MagicMock()
        result.scalar.return_value = v
        results.append(result)
    return results


class TestEmptyHintsNoActiveFilters:
    async def test_returns_none_when_no_zone_or_type_filter(self):
        """Sin filtros relajables (solo state=active default), no hay sugerencia util."""
        filters = PropertyFilters(state="active")
        hints = await property_service.get_empty_hints(AsyncMock(), filters)
        assert hints is None


class TestEmptyHintsWithTypeOnly:
    async def test_dropping_type_returns_count(self):
        """Con property_type seteado, sugerencia debe relajar el tipo."""
        filters = PropertyFilters(property_type="departamento", state="active")

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_queue_counts(8421))

        hints = await property_service.get_empty_hints(db, filters)
        assert hints is not None
        assert len(hints) == 1
        hint = hints[0]
        assert hint["drop"] == "property_type"
        assert hint["count"] == 8421


class TestEmptyHintsWithZoneOnly:
    async def test_dropping_city_returns_count(self):
        filters = PropertyFilters(city="encarnacion", state="active")

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_queue_counts(15234))

        hints = await property_service.get_empty_hints(db, filters)
        assert hints is not None
        assert len(hints) == 1
        assert hints[0]["drop"] == "city"
        assert hints[0]["count"] == 15234

    async def test_dropping_neighborhood_returns_count(self):
        filters = PropertyFilters(neighborhood="recoleta", state="active")

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_queue_counts(15500))

        hints = await property_service.get_empty_hints(db, filters)
        assert hints is not None
        assert hints[0]["drop"] == "neighborhood"
        assert hints[0]["count"] == 15500


class TestEmptyHintsWithTypeAndZone:
    async def test_returns_two_hints_one_per_relaxed_filter(self):
        """Con tipo Y barrio activos, dos sugerencias: relajar tipo y relajar zona."""
        filters = PropertyFilters(
            property_type="casa",
            city="san lorenzo",
            state="active",
        )

        db = AsyncMock()
        # Dos counts: primero relax-type, despues relax-zone.
        db.execute = AsyncMock(side_effect=_queue_counts(420, 1100))

        hints = await property_service.get_empty_hints(db, filters)
        assert hints is not None
        drops = [h["drop"] for h in hints]
        assert "property_type" in drops
        assert "city" in drops


class TestEmptyHintsZeroCountSuppressed:
    async def test_hint_with_zero_count_is_suppressed(self):
        """Si relajar un filtro tampoco aporta resultados, no se muestra."""
        filters = PropertyFilters(
            property_type="terreno",
            city="ciudad imposible",
            state="active",
        )

        db = AsyncMock()
        # Ambas relajaciones siguen dando 0.
        db.execute = AsyncMock(side_effect=_queue_counts(0, 0))

        hints = await property_service.get_empty_hints(db, filters)
        # Sin sugerencias utiles -> None
        assert hints is None

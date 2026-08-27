"""TDD — app.utils.amenities

Whitelist canonica de amenities + normalizacion (lowercase, sin acentos).
Compartida por el parser NL del panel (property_chatbot) y, en el siguiente
task, por el repositorio de busqueda.
"""
from __future__ import annotations

import pytest

from app.utils.amenities import ALLOWED_AMENITIES, normalize_amenity


class TestAllowedAmenities:
    def test_is_frozenset(self):
        assert isinstance(ALLOWED_AMENITIES, frozenset)

    def test_contains_exactly_the_canonical_ten(self):
        assert ALLOWED_AMENITIES == frozenset(
            {
                "piscina",
                "parrilla",
                "quincho",
                "gimnasio",
                "garage",
                "cochera",
                "ascensor",
                "balcon",
                "terraza",
                "vista",
            }
        )

    def test_canonicals_have_no_accents_and_are_lowercase(self):
        for amenity in ALLOWED_AMENITIES:
            assert amenity == amenity.lower()
            assert amenity.isascii()


class TestNormalizeAmenity:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("piscina", "piscina"),
            ("Piscina", "piscina"),
            ("PARRILLA", "parrilla"),
            ("Balcón", "balcon"),
            ("balcón", "balcon"),
            ("  quincho  ", "quincho"),
            ("Gimnasio ", "gimnasio"),
        ],
    )
    def test_normalizes_valid_values(self, raw, expected):
        assert normalize_amenity(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["jacuzzi", "sauna", "pileta climatizada", "", "   ", "piscina olimpica"],
    )
    def test_rejects_unknown_values(self, raw):
        assert normalize_amenity(raw) is None

    @pytest.mark.parametrize("raw", [None, 42, ["piscina"], {"a": 1}])
    def test_rejects_non_string_input(self, raw):
        assert normalize_amenity(raw) is None

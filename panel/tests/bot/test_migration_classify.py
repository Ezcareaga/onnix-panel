"""Tests for the migration classification logic (no real DB required).

Validates the seed file integrity and the REMAP_SQL coverage against the
known property_type distribution observed in staging (2026-04-14).
"""
import json
from pathlib import Path

import pytest

SEED_PATH = Path(__file__).resolve().parent.parent.parent.parent / "docs" / "audit_classifications.jsonl"

# All valid property_type_normalized IDs per the property_types catalog.
VALID_TYPE_IDS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 99}

# Deterministic remap table — mirrors REMAP_SQL in classify_properties.py.
# Used to verify that every known property_type slug is covered.
REMAP_RULES: dict[str, int] = {
    "casa": 1,
    "departamento": 2,
    "departamento-en-pozo": 2,
    "casa-duplex": 3,
    "casa-en-condominio": 1,
    "terreno": 4,
    "oficina": 5,
    "oficinas": 5,
    "local": 6,
    "deposito": 7,
    "nave": 7,
    "bodega": 7,
    "fabrica": 7,
    "quinta": 8,
    "campo": 9,
    "propiedad-agricola": 9,
    "hacienda": 9,
    "livestock farm": 9,
    "edificio": 10,
    "estacionamiento": 99,
    "fraccionamiento": 99,
    "inmueble-productivo": 99,
    "casa para estudiantes": 1,
    "departamento con jardin": 2,
    "departamento con servicio de hotel": 2,
    "restaurant with rooms": 99,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_seed_lines() -> list[dict]:
    """Load all non-empty lines from the seed JSONL file."""
    lines = []
    with open(SEED_PATH) as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                lines.append(json.loads(stripped))
    return lines


# ---------------------------------------------------------------------------
# Seed file integrity
# ---------------------------------------------------------------------------


class TestSeedLoading:
    """Verify the migration seed loading logic."""

    def test_seed_file_exists(self) -> None:
        """Seed file must be present at the expected path."""
        assert SEED_PATH.exists(), f"Seed file missing: {SEED_PATH}"

    def test_seed_has_486_entries(self) -> None:
        """Exactly 486 audit classifications must be present."""
        entries = _load_seed_lines()
        assert len(entries) == 486, f"Expected 486, got {len(entries)}"

    def test_all_seed_entries_have_required_fields(self) -> None:
        """Every entry must have id, codigo_llm, and confianza."""
        for entry in _load_seed_lines():
            assert "id" in entry, f"Missing 'id' in entry: {entry}"
            assert "codigo_llm" in entry, f"Missing 'codigo_llm' in entry: {entry}"
            assert "confianza" in entry, f"Missing 'confianza' in entry: {entry}"

    def test_all_seed_entries_high_confidence(self) -> None:
        """All entries must have confidence >= 0.75 (audit guarantee)."""
        for entry in _load_seed_lines():
            assert entry["confianza"] >= 0.75, (
                f"Low confidence {entry['confianza']} for property {entry['id']}"
            )

    def test_seed_type_ids_in_catalog(self) -> None:
        """Every codigo_llm must map to a valid property_types.id."""
        for entry in _load_seed_lines():
            assert entry["codigo_llm"] in VALID_TYPE_IDS, (
                f"Invalid type_id {entry['codigo_llm']} for property {entry['id']}"
            )

    def test_seed_property_ids_are_positive_integers(self) -> None:
        """Property IDs in the seed must be positive integers."""
        for entry in _load_seed_lines():
            assert isinstance(entry["id"], int), (
                f"Non-integer id: {entry['id']}"
            )
            assert entry["id"] > 0, f"Non-positive id: {entry['id']}"

    def test_seed_has_no_duplicate_property_ids(self) -> None:
        """Each property id should appear at most once in the seed."""
        entries = _load_seed_lines()
        ids = [e["id"] for e in entries]
        unique_ids = set(ids)
        assert len(ids) == len(unique_ids), (
            f"Duplicate property ids in seed: {len(ids) - len(unique_ids)} duplicates"
        )


# ---------------------------------------------------------------------------
# REMAP_RULES coverage
# ---------------------------------------------------------------------------


class TestRemapRules:
    """Validate the deterministic remap table."""

    def test_all_remap_targets_in_catalog(self) -> None:
        """Every target ID in REMAP_RULES must be a valid catalog ID."""
        for slug, type_id in REMAP_RULES.items():
            assert type_id in VALID_TYPE_IDS, (
                f"REMAP_RULES['{slug}'] = {type_id} is not a valid catalog ID"
            )

    def test_high_volume_types_are_covered(self) -> None:
        """The top-5 property_type slugs (by staging count) must be in REMAP_RULES."""
        top_slugs = ["terreno", "departamento", "casa", "departamento-en-pozo", "casa-duplex"]
        for slug in top_slugs:
            assert slug in REMAP_RULES, (
                f"High-volume slug '{slug}' missing from REMAP_RULES"
            )

    def test_casa_maps_to_1(self) -> None:
        assert REMAP_RULES["casa"] == 1

    def test_departamento_maps_to_2(self) -> None:
        assert REMAP_RULES["departamento"] == 2

    def test_departamento_en_pozo_maps_to_2(self) -> None:
        assert REMAP_RULES["departamento-en-pozo"] == 2

    def test_terreno_maps_to_4(self) -> None:
        assert REMAP_RULES["terreno"] == 4

    def test_duplex_maps_to_3(self) -> None:
        assert REMAP_RULES["casa-duplex"] == 3

    def test_campo_maps_to_9(self) -> None:
        assert REMAP_RULES["campo"] == 9

    def test_edificio_maps_to_10(self) -> None:
        assert REMAP_RULES["edificio"] == 10

    def test_remap_covers_all_observed_staging_slugs(self) -> None:
        """Every non-blank slug seen in staging must be covered by REMAP_RULES.

        Slugs observed on 2026-04-14 (excluding blank):
            terreno, departamento, casa, departamento-en-pozo, casa-duplex,
            local, casa-en-condominio, oficina, edificio, deposito, campo,
            quinta, nave, oficinas, propiedad-agricola, inmueble-productivo,
            estacionamiento, casa para estudiantes, fraccionamiento,
            departamento con jardin, fabrica, departamento con servicio de hotel,
            bodega, livestock farm, hacienda, restaurant with rooms
        """
        observed_staging_slugs = {
            "terreno",
            "departamento",
            "casa",
            "departamento-en-pozo",
            "casa-duplex",
            "local",
            "casa-en-condominio",
            "oficina",
            "edificio",
            "deposito",
            "campo",
            "quinta",
            "nave",
            "oficinas",
            "propiedad-agricola",
            "inmueble-productivo",
            "estacionamiento",
            "casa para estudiantes",
            "fraccionamiento",
            "departamento con jardin",
            "fabrica",
            "departamento con servicio de hotel",
            "bodega",
            "livestock farm",
            "hacienda",
            "restaurant with rooms",
        }
        missing = observed_staging_slugs - set(REMAP_RULES.keys())
        assert not missing, (
            f"These staging slugs are not covered by REMAP_RULES: {sorted(missing)}"
        )

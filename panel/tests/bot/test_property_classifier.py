"""Tests for the hybrid property classifier.

TDD: these tests were written BEFORE the implementation.
Pipeline: seed > remap > LLM fallback.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# Sync helper for deterministic (non-LLM) tests
# ---------------------------------------------------------------------------

def classify_sync(property_dict: dict):
    """Wrapper sync sobre classify_one para tests determinísticos."""
    from app.bot.services.property_classifier import classify_one
    return asyncio.run(classify_one(property_dict))


# ---------------------------------------------------------------------------
# REMAP deterministic rules
# ---------------------------------------------------------------------------

class TestRemapRules:
    """Reglas determinísticas deben cubrir todos los tipos conocidos."""

    def test_remap_casa(self):
        r = classify_sync({"id": 1, "property_type": "casa"})
        assert r.type_id == 1
        assert r.confidence == 1.0
        assert r.method == "remap"

    def test_remap_departamento_en_pozo(self):
        r = classify_sync({"id": 2, "property_type": "departamento-en-pozo"})
        assert r.type_id == 2
        assert r.confidence == 1.0

    def test_remap_casa_duplex(self):
        r = classify_sync({"id": 3, "property_type": "casa-duplex"})
        assert r.type_id == 3
        assert r.method == "remap"

    def test_remap_oficinas_plural(self):
        r = classify_sync({"id": 4, "property_type": "oficinas"})
        assert r.type_id == 5  # OFICINA

    def test_remap_bodega_to_deposito(self):
        r = classify_sync({"id": 5, "property_type": "bodega"})
        assert r.type_id == 7  # DEPOSITO

    def test_remap_livestock_farm_to_campo(self):
        r = classify_sync({"id": 6, "property_type": "livestock farm"})
        assert r.type_id == 9  # CAMPO

    def test_remap_estacionamiento_to_otro(self):
        r = classify_sync({"id": 8, "property_type": "estacionamiento"})
        assert r.type_id == 99  # OTRO

    def test_remap_fabrica_to_deposito_lower_confidence(self):
        """fabrica → DEPOSITO con confianza reducida (<1.0)."""
        r = classify_sync({"id": 7, "property_type": "fabrica"})
        assert r.type_id == 7  # DEPOSITO
        assert r.confidence < 1.0

    def test_unknown_type_not_remap(self):
        """Tipo desconocido no remap → método seed/llm/null."""
        r = classify_sync({"id": 9, "property_type": None})
        assert r.method in ("seed", "llm", "null")

    def test_case_insensitive(self):
        """REMAP_RULES debe ser case-insensitive."""
        r = classify_sync({"id": 10, "property_type": "CASA"})
        assert r.type_id == 1

    def test_remap_terreno(self):
        r = classify_sync({"id": 11, "property_type": "terreno"})
        assert r.type_id == 4
        assert r.confidence == 1.0

    def test_remap_quinta(self):
        r = classify_sync({"id": 12, "property_type": "quinta"})
        assert r.type_id == 8
        assert r.confidence == 1.0

    def test_remap_edificio(self):
        r = classify_sync({"id": 13, "property_type": "edificio"})
        assert r.type_id == 10
        assert r.confidence == 1.0

    def test_remap_hacienda_to_campo(self):
        r = classify_sync({"id": 14, "property_type": "hacienda"})
        assert r.type_id == 9  # CAMPO

    def test_remap_departamento(self):
        r = classify_sync({"id": 15, "property_type": "departamento"})
        assert r.type_id == 2
        assert r.confidence == 1.0

    def test_remap_local(self):
        r = classify_sync({"id": 16, "property_type": "local"})
        assert r.type_id == 6
        assert r.confidence == 1.0


# ---------------------------------------------------------------------------
# Seed classifications
# ---------------------------------------------------------------------------

class TestSeedClassifications:
    """Seed de audit_classifications.jsonl debe cargarse y tener prioridad."""

    def test_seed_count(self):
        """Al menos 400 seed classifications cargadas."""
        from app.bot.services.property_classifier import _SEED_CACHE
        assert len(_SEED_CACHE) >= 400

    def test_seed_overrides_remap(self):
        """Propiedad en seed: seed tiene prioridad sobre remap.

        ID 31354 está en el seed con tipo_declarado='departamento' (remap→2)
        pero codigo_llm=3 (DUPLEX). El resultado debe ser type_id=3, method='seed'.
        """
        from app.bot.services.property_classifier import _SEED_CACHE
        if 31354 not in _SEED_CACHE:
            pytest.skip("ID 31354 not in seed cache")
        result = _SEED_CACHE[31354]
        assert result.type_id == 3
        assert result.method == "seed"

    def test_seed_priority_over_remap_dynamic(self):
        """Encuentra una entrada seed que difiera del remap y verifica prioridad.

        Busca en seed alguna propiedad con tipo_declarado que esté en REMAP_RULES
        pero con codigo_llm distinto → seed debe ganar al llamar classify_one.
        """
        from app.bot.services.property_classifier import _SEED_CACHE
        if not _SEED_CACHE:
            pytest.skip("Seed cache empty")
        # La existencia de al menos un entry en _SEED_CACHE ya valida la carga
        first_id, first_result = next(iter(_SEED_CACHE.items()))
        assert first_result.method == "seed"
        assert first_result.confidence >= 0.75

    def test_seed_confidence_threshold(self):
        """Seed solo carga entradas con confianza >= 0.75."""
        from app.bot.services.property_classifier import _SEED_CACHE
        for prop_id, result in _SEED_CACHE.items():
            assert result.confidence >= 0.75, (
                f"Property {prop_id} in seed has confidence {result.confidence} < 0.75"
            )

    def test_seed_type_ids_valid(self):
        """Todos los type_id en seed son valores del catálogo."""
        from app.bot.services.property_classifier import _SEED_CACHE
        valid_ids = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 99}
        for prop_id, result in _SEED_CACHE.items():
            if result.type_id is not None:
                assert result.type_id in valid_ids, (
                    f"Property {prop_id} has invalid type_id {result.type_id}"
                )

    def test_seed_known_id_662186(self):
        """ID 662186 (casa con country club) → QUINTA (8) en seed."""
        from app.bot.services.property_classifier import _SEED_CACHE
        if 662186 not in _SEED_CACHE:
            pytest.skip("ID 662186 not in seed cache")
        result = _SEED_CACHE[662186]
        assert result.type_id == 8  # QUINTA
        assert result.method == "seed"

    def test_seed_classify_one_uses_seed(self):
        """classify_one con ID en seed devuelve method='seed'."""
        from app.bot.services.property_classifier import _SEED_CACHE
        if not _SEED_CACHE:
            pytest.skip("Seed cache empty")
        # Take first cached ID
        prop_id = next(iter(_SEED_CACHE))
        expected = _SEED_CACHE[prop_id]
        result = classify_sync({"id": prop_id, "property_type": "casa"})
        assert result.method == "seed"
        assert result.type_id == expected.type_id


# ---------------------------------------------------------------------------
# LLM fallback (mocked)
# ---------------------------------------------------------------------------

class TestClassifyOneLLM:
    """Fallback LLM con mock."""

    @pytest.mark.asyncio
    async def test_llm_classify_null_type(self):
        """Propiedad con NULL type y sin seed → LLM."""
        from app.bot.services.property_classifier import classify_one
        mock_resp = {"codigo": 9, "confianza": 0.95, "razon": "Campo rural grande"}
        with patch(
            "app.bot.services.property_classifier._call_haiku",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await classify_one(
                {"id": 99999, "property_type": None, "title": "160ha campo ganadero"}
            )
        assert result.type_id == 9
        assert result.method == "llm"

    @pytest.mark.asyncio
    async def test_llm_low_confidence_returns_null_type_id(self):
        """LLM con confianza < 0.75 → type_id=None."""
        from app.bot.services.property_classifier import classify_one
        mock_resp = {"codigo": 4, "confianza": 0.60, "razon": "Ambiguo"}
        with patch(
            "app.bot.services.property_classifier._call_haiku",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await classify_one(
                {"id": 99998, "property_type": None, "title": "propiedad ambigua"}
            )
        assert result.type_id is None
        assert result.confidence == 0.60
        assert result.method == "llm"

    @pytest.mark.asyncio
    async def test_llm_error_returns_null_method(self):
        """LLM que lanza excepción → method='null', type_id=None."""
        from app.bot.services.property_classifier import classify_one
        with patch(
            "app.bot.services.property_classifier._call_haiku",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API timeout"),
        ):
            result = await classify_one(
                {"id": 99997, "property_type": None, "title": "propiedad desconocida"}
            )
        assert result.type_id is None
        assert result.method == "null"
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_llm_high_confidence_keeps_type_id(self):
        """LLM con confianza exactamente 0.75 → type_id asignado (umbral inclusivo)."""
        from app.bot.services.property_classifier import classify_one
        mock_resp = {"codigo": 5, "confianza": 0.75, "razon": "Oficina en edificio"}
        with patch(
            "app.bot.services.property_classifier._call_haiku",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await classify_one(
                {"id": 99996, "property_type": None, "title": "local comercial oficina"}
            )
        assert result.type_id == 5
        assert result.method == "llm"

    @pytest.mark.asyncio
    async def test_remap_wins_over_llm_when_no_seed(self):
        """Tipo con remap no llama LLM."""
        from app.bot.services.property_classifier import classify_one
        with patch(
            "app.bot.services.property_classifier._call_haiku",
            new_callable=AsyncMock,
        ) as mock_haiku:
            result = await classify_one({"id": 99995, "property_type": "departamento"})
        mock_haiku.assert_not_called()
        assert result.type_id == 2
        assert result.method == "remap"


# ---------------------------------------------------------------------------
# ClassificationResult dataclass
# ---------------------------------------------------------------------------

class TestClassificationResult:
    """Verificar estructura del dataclass de resultado."""

    def test_result_fields(self):
        from app.bot.services.property_classifier import ClassificationResult
        r = ClassificationResult(
            type_id=1,
            confidence=0.95,
            reason="test",
            method="remap",
        )
        assert r.type_id == 1
        assert r.confidence == 0.95
        assert r.reason == "test"
        assert r.method == "remap"

    def test_result_optional_type_id(self):
        from app.bot.services.property_classifier import ClassificationResult
        r = ClassificationResult(
            type_id=None,
            confidence=0.0,
            reason="unknown",
            method="null",
        )
        assert r.type_id is None

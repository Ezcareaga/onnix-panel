"""Unit tests for ToolExecutor._execute_resolver_zona (M5.1 Paso 1).

Tests:
1. test_texto_vacio_retorna_error
2. test_ciudad_directa
3. test_alias_city
4. test_barrio_con_contexto_ciudad
5. test_barrio_sin_contexto_ciudad
6. test_landmark_simple
7. test_ciudad_devuelve_vecinos
8. test_texto_completamente_desconocido
9. test_dispatch_resolver_zona
"""
from __future__ import annotations

import os


from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.ai.types import ToolCall
from app.bot.core.tool_executor import ToolExecutor
from app.bot.core.types import ConversationState
from app.bot.search.geo_resolver import GeoResolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_executor() -> ToolExecutor:
    """Build a ToolExecutor with a real GeoResolver and a mock SearchService."""
    geo = GeoResolver()

    search_service = MagicMock()
    search_service._geo_resolver = geo
    # search_properties is async — not needed for resolver_zona tests but
    # the attribute must exist so ToolExecutor doesn't error on instantiation.
    search_service.search_properties = AsyncMock()

    return ToolExecutor(search_service=search_service)


def _ctx_with_ciudad(ciudad: str) -> ConversationState:
    return ConversationState(filtros={"ciudad": ciudad})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResolverZona:
    """Unit tests for _execute_resolver_zona."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.executor = _make_executor()
        self.session = AsyncMock()

    # 1. Texto vacío
    @pytest.mark.asyncio
    async def test_texto_vacio_retorna_error(self):
        """Input vacío debe retornar error."""
        result = await self.executor._execute_resolver_zona(
            {"texto": ""},
            self.session,
        )
        assert "error" in result
        assert result["error"] == "texto requerido"

    # 2. Ciudad directa
    @pytest.mark.asyncio
    async def test_ciudad_directa(self):
        """Input 'asuncion' debe resolver ciudad_canonica='asuncion'."""
        result = await self.executor._execute_resolver_zona(
            {"texto": "asuncion"},
            self.session,
        )
        assert result["ciudad_canonica"] == "asuncion"
        assert result["barrio_canonico"] is None

    # 3. Alias de ciudad
    @pytest.mark.asyncio
    async def test_alias_city(self):
        """Input 'san ber' (alias) debe resolver ciudad_canonica='san bernardino'."""
        result = await self.executor._execute_resolver_zona(
            {"texto": "san ber"},
            self.session,
        )
        assert result["ciudad_canonica"] == "san bernardino"

    # 4. Barrio con contexto de ciudad
    @pytest.mark.asyncio
    async def test_barrio_con_contexto_ciudad(self):
        """Input 'villa morra' con ciudad='asuncion' en contexto debe resolver barrio."""
        ctx = _ctx_with_ciudad("asuncion")
        result = await self.executor._execute_resolver_zona(
            {"texto": "villa morra"},
            self.session,
            search_context=ctx,
        )
        # villa morra is a known barrio in Asuncion
        assert result["barrio_canonico"] is not None
        assert result["ciudad_canonica"] is None  # no es ciudad

    # 5. Barrio sin contexto de ciudad
    @pytest.mark.asyncio
    async def test_barrio_sin_contexto_ciudad(self):
        """Input 'villa morra' sin ciudad en contexto: barrio_canonico debe ser None."""
        result = await self.executor._execute_resolver_zona(
            {"texto": "villa morra"},
            self.session,
            search_context=None,
        )
        # Without city context we cannot safely resolve barrio
        assert result["barrio_canonico"] is None

    # 6. Landmark simple
    @pytest.mark.asyncio
    async def test_landmark_simple(self):
        """Input de un landmark conocido debe retornar landmark_detected no-None."""
        # "shopping del sol" is a known landmark in Asuncion's landmarks file
        result = await self.executor._execute_resolver_zona(
            {"texto": "shopping del sol"},
            self.session,
        )
        assert result["landmark_detected"] is not None or result["landmark_ciudad"] is not None, (
            "Expected landmark to be resolved for 'shopping del sol'"
        )

    # 7. Ciudad con vecinos
    @pytest.mark.asyncio
    async def test_ciudad_devuelve_vecinos(self):
        """Input 'luque' debe retornar barrios_cercanos con al menos 1 vecino."""
        result = await self.executor._execute_resolver_zona(
            {"texto": "luque"},
            self.session,
        )
        assert result["ciudad_canonica"] == "luque"
        assert isinstance(result["barrios_cercanos"], list)
        assert len(result["barrios_cercanos"]) >= 1

    # 8. Texto completamente desconocido
    @pytest.mark.asyncio
    async def test_texto_completamente_desconocido(self):
        """Input sin sentido debe retornar todos los campos None / lista vacía."""
        result = await self.executor._execute_resolver_zona(
            {"texto": "jdsjasjkd"},
            self.session,
        )
        assert result["ciudad_canonica"] is None
        assert result["barrio_canonico"] is None
        assert result["landmark_detected"] is None
        assert result["landmark_ciudad"] is None
        assert isinstance(result["barrios_cercanos"], list)
        assert "interpretation" in result

    # 9. Barrio inválido con ciudad en contexto retorna None (Fix 1)
    @pytest.mark.asyncio
    async def test_barrio_invalido_retorna_none(self):
        """Input 'jdsjasjkd' con ciudad='asuncion' en contexto NO debe resolver barrio.

        Antes del fix, resolve_barrio_alias devolvía 'jdsjasjkd' sin validar,
        confundiendo a Claude con un barrio 'válido'. Ahora debe retornar None.
        """
        ctx = _ctx_with_ciudad("asuncion")
        result = await self.executor._execute_resolver_zona(
            {"texto": "jdsjasjkd"},
            self.session,
            search_context=ctx,
        )
        assert result["barrio_canonico"] is None, (
            "Barrio inválido no debe resolverse; esperado None pero fue "
            f"{result['barrio_canonico']!r}"
        )

    # 10. LandmarkResult con barrio vacío retorna landmark_detected=None (Fix 2)
    @pytest.mark.asyncio
    async def test_landmark_sin_barrio_retorna_none(self):
        """Si LandmarkResult.barrio == '', landmark_detected debe ser None.

        GeoResolver.resolve_landmark asigna barrio=record['barrio'] or '', por lo
        que un landmark sin barrio en el JSON llega como ''. Fix 2 mapea '' → None.
        """
        from unittest.mock import patch
        from app.bot.search.geo_resolver import LandmarkResult

        landmark_sin_barrio = LandmarkResult(
            ciudad="asuncion",
            barrio="",  # Edge case: barrio vacío
            barrios_cercanos=[],
        )

        executor = _make_executor()
        session = AsyncMock()

        with patch.object(
            executor._search_service._geo_resolver,
            "resolve_landmark",
            return_value=landmark_sin_barrio,
        ):
            result = await executor._execute_resolver_zona(
                {"texto": "algun landmark sin barrio"},
                session,
            )

        assert result["landmark_detected"] is None, (
            "landmark_detected con barrio='' debe ser None, no cadena vacía"
        )
        assert result["landmark_ciudad"] == "asuncion"

    # 11. Landmark sin contexto de ciudad debe poblar barrios_cercanos
    # desde el barrio del landmark (FIX 2 — 2026-04-26)
    @pytest.mark.asyncio
    async def test_landmark_shopping_del_sol_devuelve_vecinos(self):
        """Para 'shopping del sol' sin contexto, barrios_cercanos debe
        contener los vecinos de manora.

        Antes del fix, ciudad_canonica y barrio_canonico eran None (porque
        el texto es un landmark, no una ciudad/barrio), y el loop de vecinos
        no ejecutaba ninguna rama → barrios_cercanos = []. El fix usa
        landmark.barrio + landmark.ciudad para computar vecinos cuando hay
        landmark detectado y la lista quedó vacía.
        """
        result = await self.executor._execute_resolver_zona(
            {"texto": "shopping del sol"},
            self.session,
            search_context=None,
        )
        assert result["landmark_ciudad"] == "asuncion"
        assert result["landmark_detected"] == "manora"
        assert result["barrios_cercanos"] == [
            "las lomas",
            "san jorge",
            "santo domingo",
            "villa morra",
            "ycua sati",
        ]


# ---------------------------------------------------------------------------
# Dispatch test
# ---------------------------------------------------------------------------

class TestDispatchResolverZona:
    """Test that the dispatch table routes resolver_zona to _execute_resolver_zona."""

    @pytest.mark.asyncio
    async def test_dispatch_resolver_zona(self):
        """ToolCall con name='resolver_zona' ejecuta y retorna la forma esperada."""
        executor = _make_executor()
        session = AsyncMock()
        ctx = ConversationState()

        tool_call = ToolCall(
            id="toolu_rz_01",
            name="resolver_zona",
            input={"texto": "asuncion"},
        )

        result = await executor.execute(tool_call, session, search_context=ctx)

        assert "ciudad_canonica" in result
        assert "barrio_canonico" in result
        assert "interpretation" in result
        assert result["ciudad_canonica"] == "asuncion"

"""
Tests for app/services/stats_service.py

All repository calls are fully mocked so no real database connection is
required.  The three new repo methods (message_repo.count_per_day,
bot_error_repo.count_per_day, contact_repo.count_by_status_for_source) are
mocked at the module-import level.

Coverage target: lines 9-44 (the entire get_stats body).
"""
import pytest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.stats_service import StatsService
from app.tz import PYT


# ---------------------------------------------------------------------------
# Patch targets (module-level repos imported inside stats_service)
# ---------------------------------------------------------------------------

_CONTACT_REPO = "app.services.stats_service.contact_repo"
_LEAD_EVENT_REPO = "app.services.stats_service.lead_event_repo"
_MESSAGE_REPO = "app.services.stats_service.message_repo"
_BOT_ERROR_REPO = "app.services.stats_service.bot_error_repo"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_all_repos(
    count_by_source=None,
    weekly_evolution=None,
    count_today=0,
    count_by_type_this_week=None,
    messages_per_day=None,
    errors_per_day=None,
    conversion_status_counts=None,
):
    """Return a context manager that patches all repo calls with given values."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with (
            patch(f"{_CONTACT_REPO}.count_by_source", new=AsyncMock(return_value=count_by_source or [])),
            patch(f"{_CONTACT_REPO}.weekly_evolution", new=AsyncMock(return_value=weekly_evolution or [])) as mock_weekly,
            patch(f"{_CONTACT_REPO}.count_today", new=AsyncMock(return_value=count_today)),
            patch(f"{_CONTACT_REPO}.count_by_status_for_source", new=AsyncMock(return_value=conversion_status_counts or {})) as mock_conv,
            patch(f"{_LEAD_EVENT_REPO}.count_by_type_this_week", new=AsyncMock(return_value=count_by_type_this_week or {})),
            patch(f"{_MESSAGE_REPO}.count_per_day", new=AsyncMock(return_value=messages_per_day or [])) as mock_msgs,
            patch(f"{_BOT_ERROR_REPO}.count_per_day", new=AsyncMock(return_value=errors_per_day or [])) as mock_errs,
        ):
            yield {
                "weekly": mock_weekly,
                "conversion": mock_conv,
                "msgs": mock_msgs,
                "errs": mock_errs,
            }
    return _ctx()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetStatsDefaultDays:
    """get_stats() with default days=7 returns a well-formed dict."""

    async def test_returns_all_required_keys(self):
        db = AsyncMock()
        with _patch_all_repos():
            result = await StatsService.get_stats(db)

        expected_keys = {
            "leads_by_source",
            "weekly_evolution",
            "events_this_week",
            "new_today",
            "days",
            "messages_per_day",
            "errors_per_day",
            "conversion_rate",
            "conversion_total",
            "conversion_converted",
        }
        assert expected_keys == set(result.keys())

    async def test_default_days_is_7(self):
        db = AsyncMock()
        with _patch_all_repos():
            result = await StatsService.get_stats(db)

        assert result["days"] == 7


class TestGetStatsCustomDays:
    """Custom days parameter is forwarded to every downstream call."""

    async def test_custom_days_propagated_to_result(self):
        db = AsyncMock()
        with _patch_all_repos() as mocks:
            result = await StatsService.get_stats(db, days=30)

        assert result["days"] == 30
        mocks["weekly"].assert_awaited_once_with(db, days=30)

    async def test_repo_methods_receive_correct_days_param(self):
        db = AsyncMock()
        with _patch_all_repos() as mocks:
            await StatsService.get_stats(db, days=14)

        mocks["msgs"].assert_awaited_once_with(db, days=14)
        mocks["errs"].assert_awaited_once_with(db, days=14)


class TestConversionRateZeroTotal:
    """When total_ic == 0 the service must not divide by zero and returns 0."""

    async def test_zero_total_returns_zero_rate(self):
        db = AsyncMock()
        with _patch_all_repos(conversion_status_counts={}):
            result = await StatsService.get_stats(db)

        assert result["conversion_rate"] == 0
        assert result["conversion_total"] == 0
        assert result["conversion_converted"] == 0


class TestConversionRateWithData:
    """Conversion rate is correctly calculated from status_counts."""

    async def test_conversion_rate_calculated_correctly(self):
        # 10 new, 5 interested, 3 visit_scheduled, 2 closed = (5 + 3 + 2)/20 = 50.0%
        # M6.2 (OQ-5): visit_scheduled SUMA como converted post-mig-040.
        conv_data = {"new": 10, "interested": 5, "visit_scheduled": 3, "closed": 2}
        db = AsyncMock()
        with _patch_all_repos(conversion_status_counts=conv_data):
            result = await StatsService.get_stats(db)

        assert result["conversion_total"] == 20
        assert result["conversion_converted"] == 10
        assert result["conversion_rate"] == 50.0

    async def test_conversion_rate_rounded_to_one_decimal(self):
        # 1 closed out of 3 = 33.333...% = rounds to 33.3
        conv_data = {"new": 2, "closed": 1}
        db = AsyncMock()
        with _patch_all_repos(conversion_status_counts=conv_data):
            result = await StatsService.get_stats(db)

        assert result["conversion_rate"] == 33.3

    async def test_only_interested_and_closed_count_as_converted(self):
        # contacted and negotiation must NOT be counted as converted
        conv_data = {"contacted": 5, "negotiation": 5, "interested": 2}
        db = AsyncMock()
        with _patch_all_repos(conversion_status_counts=conv_data):
            result = await StatsService.get_stats(db)

        assert result["conversion_converted"] == 2
        assert result["conversion_total"] == 12

    async def test_conversion_rate_includes_visit_scheduled(self):
        """M6.2 (OQ-5): visit_scheduled IS counted as converted post-mig-040.
        Honors ROADMAP §M6.2 — agendar visita es una conversión.
        """
        conv_data = {"new": 5, "interested": 2, "visit_scheduled": 99, "closed": 1}
        db = AsyncMock()
        with _patch_all_repos(conversion_status_counts=conv_data):
            result = await StatsService.get_stats(db)
        # 2 interested + 99 visit_scheduled + 1 closed = 102
        assert result["conversion_converted"] == 102
        assert result["conversion_total"] == 107


def _hoy_pyt() -> date:
    """El "hoy" de las series es el paraguayo desde la tanda 12."""
    return datetime.now(PYT).date()


class TestMessagesAndErrorsPerDay:
    """La serie tiene un punto por dia, y el numero del repo cae en su dia."""

    async def test_messages_per_day_from_repo(self):
        hoy = _hoy_pyt()
        msgs_data = [{"day": str(hoy), "count": 42}]
        db = AsyncMock()
        with _patch_all_repos(messages_per_day=msgs_data):
            result = await StatsService.get_stats(db)

        serie = result["messages_per_day"]
        assert len(serie) == 7
        assert serie[-1] == {"day": str(hoy), "count": 42}
        assert [f["count"] for f in serie[:-1]] == [0] * 6

    async def test_errors_per_day_from_repo(self):
        hoy = _hoy_pyt()
        errs_data = [{"day": str(hoy), "count": 3}]
        db = AsyncMock()
        with _patch_all_repos(errors_per_day=errs_data):
            result = await StatsService.get_stats(db)

        serie = result["errors_per_day"]
        assert len(serie) == 7
        assert serie[-1] == {"day": str(hoy), "count": 3}
        assert [f["count"] for f in serie[:-1]] == [0] * 6


class TestEmptyResultSets:
    """All repo calls returning empty data produce correct zero-state output."""

    async def test_empty_leads_by_source(self):
        db = AsyncMock()
        with _patch_all_repos():
            result = await StatsService.get_stats(db)

        assert result["leads_by_source"] == []
        # Sin actividad la serie NO es una lista vacia: son siete dias en cero.
        # Una lista vacia dibujaba una card en blanco donde la respuesta real
        # es "cero todos los dias", que no es lo mismo que "sin datos".
        for clave in ("weekly_evolution", "messages_per_day", "errors_per_day"):
            serie = result[clave]
            assert len(serie) == 7, clave
            assert all(f["count"] == 0 for f in serie), clave
        assert result["new_today"] == 0
        assert result["events_this_week"] == {}

    async def test_weekly_evolution_mapped_to_dict_list(self):
        today = _hoy_pyt()
        weekly_data = [(today, 5)]
        db = AsyncMock()
        with _patch_all_repos(
            weekly_evolution=weekly_data,
            count_today=7,
            count_by_type_this_week={"lead": 3},
        ):
            result = await StatsService.get_stats(db)

        serie = result["weekly_evolution"]
        assert len(serie) == 7
        assert serie[-1] == {"day": str(today), "count": 5}
        assert result["new_today"] == 7
        assert result["events_this_week"] == {"lead": 3}


class TestConversionRepoCalledWithInfocasas:
    """Ensure the conversion query uses 'infocasas' as the source."""

    async def test_count_by_status_for_source_called_with_infocasas(self):
        db = AsyncMock()
        with _patch_all_repos() as mocks:
            await StatsService.get_stats(db)

        mocks["conversion"].assert_awaited_once_with(db, "infocasas")


# ---------------------------------------------------------------------------
# Template-level assertions (synchronous; read the partial directly)
# ---------------------------------------------------------------------------

def test_stats_counters_legend_does_not_mention_visita():
    """Legend in stats_counters.html must read '(interesado + cerrado)' — no 'visita'."""
    from pathlib import Path

    template_path = (
        Path(__file__).resolve().parent.parent
        / "app" / "templates" / "partials" / "stats_counters.html"
    )
    content = template_path.read_text(encoding="utf-8")

    assert "(interesado + cerrado)" in content, (
        "Expected legend '(interesado + cerrado)' in stats_counters.html"
    )
    assert "visita" not in content.lower(), (
        "Legend must not mention 'visita' (case-insensitive) after CLEAN-02"
    )


# ===========================================================================
# Carril I — las series diarias omitian los dias en cero
# ===========================================================================
#
# Un `GROUP BY` por dia devuelve solo los dias que tuvieron algo. La
# barra del martes quedaba pegada a la del viernes sin que nada dijera que
# entre las dos hubo tres dias en cero: el grafico mentia sobre el ritmo, que
# es lo unico que un grafico de barras por dia sirve para mostrar.
#
# El relleno vive en el servicio y no en los tres repos, que es donde
# convergen las tres series. El patron es el de
# metrics_repository.ai_cost_by_day_last_7d.

from app.services.stats_service import _serie_por_dia


class TestSeriePorDia:
    def test_devuelve_exactamente_los_dias_pedidos(self):
        assert len(_serie_por_dia([], 30)) == 30
        assert len(_serie_por_dia([], 7)) == 7
        assert len(_serie_por_dia([], 1)) == 1

    def test_las_fechas_son_contiguas_y_ascendentes(self):
        serie = _serie_por_dia([], 10)
        fechas = [date.fromisoformat(f["day"]) for f in serie]
        assert fechas == sorted(fechas)
        saltos = {(b - a).days for a, b in zip(fechas, fechas[1:])}
        assert saltos == {1}, f"la serie tiene huecos de {sorted(saltos)} dias"

    def test_termina_hoy(self):
        """Hoy es el dia PARAGUAYO: los repos bucketean en PYT (tanda 12)."""
        serie = _serie_por_dia([], 5)
        assert serie[-1]["day"] == str(datetime.now(PYT).date())

    def test_el_hueco_del_medio_sale_en_cero_y_no_desaparece(self):
        """El caso que delataba el bug: dos dias con actividad y un pozo."""
        hoy = _hoy_pyt()
        filas = [
            {"day": str(hoy - timedelta(days=4)), "count": 9},
            {"day": str(hoy), "count": 4},
        ]
        serie = _serie_por_dia(filas, 5)
        assert [f["count"] for f in serie] == [9, 0, 0, 0, 4]

    def test_acepta_tuplas_y_objetos_date(self):
        """weekly_evolution devuelve tuplas (date, count); los otros dos, dicts."""
        hoy = _hoy_pyt()
        assert _serie_por_dia([(hoy, 3)], 2)[-1]["count"] == 3
        assert _serie_por_dia([{"day": hoy, "count": 3}], 2)[-1]["count"] == 3
        assert _serie_por_dia([{"day": str(hoy), "count": 3}], 2)[-1]["count"] == 3

    def test_lo_que_cae_fuera_de_la_ventana_no_entra(self):
        """La ventana la manda el titulo de la card, no el dato mas viejo."""
        viejo = _hoy_pyt() - timedelta(days=400)
        serie = _serie_por_dia([{"day": str(viejo), "count": 99}], 7)
        assert len(serie) == 7
        assert all(f["count"] == 0 for f in serie)

    def test_no_se_pierde_ningun_conteo_de_la_ventana(self):
        hoy = _hoy_pyt()
        filas = [{"day": str(hoy - timedelta(days=n)), "count": n + 1} for n in range(7)]
        serie = _serie_por_dia(filas, 7)
        assert sum(f["count"] for f in serie) == sum(range(1, 8))

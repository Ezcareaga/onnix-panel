"""
Tests for stats_service.get_gap_analysis + property_repo.count_active_by_city_type.

Gap oferta/demanda — por cada combo ciudad×tipo del top de demanda del
período, cuántas propiedades ACTIVAS (is_active, on_hold=false,
duplicate_of IS NULL) tenemos en stock.

Dos niveles:
- Unit (repos mockeados): lógica de combos, threshold 'captar', honestidad.
- Integración (db fixture, onnix_dev snapshot): asserts estructurales.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.stats_service import stats_service
from app.repositories.property_repo import StockCombo

_SVC = "app.services.stats_service"


def _row(city, ptype, operation="venta", source="infocasas"):
    """Demand row como las devuelven lead_repo/conversation_repo."""
    def _key(s):
        return s.lower().strip() if s else None
    return {
        "city": city, "city_key": _key(city),
        "ptype": ptype, "ptype_key": _key(ptype),
        "operation": operation, "source": source,
    }


def _patch_repos(demand_rows=None, filter_rows=None, stock=None):
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with (
            patch(f"{_SVC}.lead_repo.get_demand_rows",
                  new=AsyncMock(return_value=demand_rows or [])),
            patch(f"{_SVC}.conversation_repo.get_demand_filter_rows",
                  new=AsyncMock(return_value=filter_rows or [])),
            patch(f"{_SVC}.property_repo.count_active_by_city_type",
                  new=AsyncMock(return_value=stock or {})) as mock_stock,
        ):
            yield mock_stock
    return _ctx()


class TestGapAnalysisUnit:
    async def test_returns_required_keys(self):
        db = AsyncMock()
        with _patch_repos():
            gap = await stats_service.get_gap_analysis(db)
        for key in ("days", "rows", "total_combos"):
            assert key in gap, key

    async def test_days_passthrough(self):
        db = AsyncMock()
        with _patch_repos():
            gap = await stats_service.get_gap_analysis(db, days=90)
        assert gap["days"] == 90

    async def test_combos_counted_and_sorted_desc(self):
        rows = (
            [_row("Asunción", "casa")] * 3
            + [_row("Luque", "terreno")] * 5
            + [_row("Asunción", "departamento")]
        )
        db = AsyncMock()
        with _patch_repos(demand_rows=rows):
            gap = await stats_service.get_gap_analysis(db)
        demands = [r["demand"] for r in gap["rows"]]
        assert demands == sorted(demands, reverse=True)
        assert gap["rows"][0]["demand"] == 5
        assert gap["total_combos"] == 3

    async def test_rows_without_city_or_type_excluded(self):
        rows = [
            _row("Asunción", "casa"),
            _row(None, "casa"),          # sin ciudad → no es combo
            _row("Asunción", None),      # sin tipo → no es combo
            _row("", ""),                # claves vacías → NULLIF en repo real
        ]
        db = AsyncMock()
        with _patch_repos(demand_rows=rows):
            gap = await stats_service.get_gap_analysis(db)
        assert gap["total_combos"] == 1
        assert len(gap["rows"]) == 1

    async def test_captar_threshold_stock_below_half_demand(self):
        """Chip 'captar' si stock < demanda/2; neutro si no (incluye igual)."""
        rows = [_row("Asunción", "casa")] * 10 + [_row("Luque", "terreno")] * 10
        stock = {("asunción", "casa"): StockCombo(4, "casa"),
                 ("luque", "terreno"): StockCombo(5, "terreno")}
        db = AsyncMock()
        with _patch_repos(demand_rows=rows, stock=stock):
            gap = await stats_service.get_gap_analysis(db)
        by_city = {r["city"]: r for r in gap["rows"]}
        assert by_city["Asunción"]["captar"] is True    # 4 < 10/2
        assert by_city["Luque"]["captar"] is False      # 5 == 10/2 → neutro

    async def test_stock_defaults_to_zero_when_no_match(self):
        rows = [_row("Areguá", "quinta")] * 2
        db = AsyncMock()
        with _patch_repos(demand_rows=rows, stock={}):
            gap = await stats_service.get_gap_analysis(db)
        assert gap["rows"][0]["stock"] == 0
        assert gap["rows"][0]["captar"] is True

    async def test_limit_caps_rows(self):
        rows = [_row(f"Ciudad{i}", "casa") for i in range(12)]
        db = AsyncMock()
        with _patch_repos(demand_rows=rows):
            gap = await stats_service.get_gap_analysis(db, limit=8)
        assert len(gap["rows"]) <= 8
        assert gap["total_combos"] == 12

    async def test_merges_lead_and_filter_rows(self):
        """Demanda = leads (IC + bot) + búsquedas con filtros, mismo universo
        que get_demand_stats del dashboard."""
        db = AsyncMock()
        with _patch_repos(
            demand_rows=[_row("Asunción", "casa")],
            filter_rows=[_row("asuncion", "casa", source="whatsapp")],
        ):
            gap = await stats_service.get_gap_analysis(db)
        # 'Asunción' y 'asuncion' agrupan por city_key... claves distintas en
        # este mock simplificado (sin unaccent python); al menos no explota y
        # suma 2 consultas en total
        assert sum(r["demand"] for r in gap["rows"]) == 2


class TestGapAnalysisIntegration:
    """Contra onnix_dev (snapshot real) — estructural, no números exactos."""

    async def test_structure_and_consistency(self, db):
        gap = await stats_service.get_gap_analysis(db, days=90)
        assert gap["days"] == 90
        assert len(gap["rows"]) <= 8
        for row in gap["rows"]:
            assert row["city"] and row["ptype"]
            assert row["demand"] > 0
            assert row["stock"] >= 0
            assert row["captar"] == (row["stock"] < row["demand"] / 2)

    async def test_wider_window_never_shrinks_combos(self, db):
        g30 = await stats_service.get_gap_analysis(db, days=30)
        g365 = await stats_service.get_gap_analysis(db, days=365)
        assert g365["total_combos"] >= g30["total_combos"]


class TestCountActiveByCityType:
    """property_repo.count_active_by_city_type — stock activo por combo.

    Tipo con match parcial bidireccional (unaccent/lower) porque los tipos
    de demanda IC no comparten slugs con properties: 'duplex' ↔
    'casa-duplex', 'locales comerciales' ↔ 'local', 'tinglado o deposito'
    ↔ 'deposito'.
    """

    async def test_empty_pairs_returns_empty(self, db):
        from app.repositories.property_repo import property_repo
        assert await property_repo.count_active_by_city_type(db, []) == {}

    async def test_known_combo_has_stock(self, db):
        """asuncion×casa: top ciudad y top tipo del snapshot — debe dar > 0."""
        from app.repositories.property_repo import property_repo
        counts = await property_repo.count_active_by_city_type(
            db, [("asuncion", "casa")],
        )
        assert counts[("asuncion", "casa")].stock > 0

    async def test_unknown_type_returns_zero(self, db):
        from app.repositories.property_repo import property_repo
        counts = await property_repo.count_active_by_city_type(
            db, [("asuncion", "tipo-inexistente-xyz")],
        )
        assert counts[("asuncion", "tipo-inexistente-xyz")].stock == 0
        assert counts[("asuncion", "tipo-inexistente-xyz")].slug is None, (
            "sin stock no puede haber slug: el link se queda con la ciudad"
        )

    async def test_partial_match_ic_type_to_property_slug(self, db):
        """'duplex' (tipo IC) debe matchear 'casa-duplex' (slug properties)."""
        from app.repositories.property_repo import property_repo
        counts = await property_repo.count_active_by_city_type(
            db, [("asuncion", "duplex")],
        )
        assert counts[("asuncion", "duplex")].stock >= 0  # estructural
        # global: hay 462 casa-duplex activos en el snapshot, alguna en
        # ciudades top — el match parcial no puede dar 0 en todas
        cities = ["asuncion", "luque", "lambare", "fernando de la mora",
                  "san lorenzo", "encarnacion"]
        counts = await property_repo.count_active_by_city_type(
            db, [(c, "duplex") for c in cities],
        )
        assert sum(c.stock for c in counts.values()) > 0

    async def test_all_requested_pairs_present_in_result(self, db):
        from app.repositories.property_repo import property_repo
        pairs = [("asuncion", "casa"), ("luque", "terreno"),
                 ("nocity-xyz", "casa")]
        counts = await property_repo.count_active_by_city_type(db, pairs)
        assert set(counts) == set(pairs)
        assert counts[("nocity-xyz", "casa")].stock == 0


# ===========================================================================
# Carril I — «Captar» pasa a ser un link, y filtra por lo que se conto
# ===========================================================================
#
# «Captar» era la unica celda de la tabla que nombraba un trabajo y no llevaba
# a hacerlo. El link va a /properties, y ahi esta la trampa: ese listado filtra
# con `property_type = :valor` EXACTO, mientras el conteo de stock de esta
# tabla matchea parcial y sin acentos —'duplex' cuenta 'casa-duplex'—. Mandarle
# la etiqueta de la demanda daria una lista vacia sobre una fila que dice
# stock 3: otra vez dos numeros distintos en la misma pantalla.
#
# Por eso la consulta que cuenta el stock devuelve tambien el slug con el que
# lo conto, y el link manda ese.

class TestSlugParaElLink:
    async def test_el_slug_es_un_property_type_de_verdad(self, db):
        """Con stock > 0 el slug tiene que existir en `properties`."""
        from sqlalchemy import text as _t
        from app.repositories.property_repo import property_repo

        counts = await property_repo.count_active_by_city_type(
            db, [("asuncion", "casa"), ("asuncion", "duplex")],
        )
        tipos = {
            r[0] for r in (await db.execute(_t(
                "SELECT DISTINCT property_type FROM properties "
                "WHERE is_active AND NOT on_hold AND duplicate_of IS NULL"
            ))).all() if r[0]
        }
        con_stock = [c for c in counts.values() if c.stock > 0]
        assert con_stock, "el snapshot no tiene stock en asuncion: revisar la base de test"
        for combo in con_stock:
            assert combo.slug in tipos, (
                f"el slug {combo.slug!r} no es un property_type de la base: "
                f"el link a /properties daria una lista vacia"
            )

    async def test_el_slug_filtra_a_lo_que_se_conto(self, db):
        """La prueba que cierra el circulo: filtrar por el slug no da cero."""
        from sqlalchemy import text as _t
        from app.repositories.property_repo import property_repo

        counts = await property_repo.count_active_by_city_type(db, [("asuncion", "duplex")])
        combo = counts[("asuncion", "duplex")]
        if combo.stock == 0:
            pytest.skip(
                "el snapshot no tiene duplex activos en Asuncion; sin stock no "
                "hay slug que verificar"
            )
        n = (await db.execute(_t(
            "SELECT count(*) FROM properties WHERE is_active AND NOT on_hold "
            "AND duplicate_of IS NULL AND property_type = :slug "
            "AND unaccent(city) ILIKE unaccent(:city)"
        ), {"slug": combo.slug, "city": "%asuncion%"})).scalar()
        assert n > 0, (
            f"filtrando /properties por property_type={combo.slug!r} y "
            f"city=asuncion la lista da {n}, y la tabla dice stock {combo.stock}"
        )

    async def test_la_fila_del_gap_trae_el_slug(self):
        rows = [_row("Asunción", "duplex")] * 10
        stock = {("asunción", "duplex"): StockCombo(3, "casa-duplex")}
        db = AsyncMock()
        with _patch_repos(demand_rows=rows, stock=stock):
            gap = await stats_service.get_gap_analysis(db)
        fila = gap["rows"][0]
        assert fila["ptype"] == "duplex", "la etiqueta visible sigue siendo la de la demanda"
        assert fila["ptype_slug"] == "casa-duplex", "el link tiene que ir con el slug"

    async def test_sin_stock_no_hay_slug(self):
        rows = [_row("Areguá", "quinta")] * 2
        db = AsyncMock()
        with _patch_repos(demand_rows=rows, stock={}):
            gap = await stats_service.get_gap_analysis(db)
        assert gap["rows"][0]["ptype_slug"] is None, (
            "sin stock el link se queda con la ciudad, no inventa un tipo"
        )


class TestElLinkDeCaptar:
    """La celda «Captar», renderizada.

    `base.html` se stubea a un bloque de contenido pelado: lo que se mide es la
    tabla del gap, no el shell.
    """

    @staticmethod
    def _render(rows: list[dict]) -> str:
        from pathlib import Path
        from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

        tpl = Path(__file__).resolve().parent.parent / "app" / "templates"
        env = Environment(
            loader=ChoiceLoader([
                DictLoader({
                    "base.html": "{% block page_actions %}{% endblock %}{% block content %}{% endblock %}",
                    "partials/demand_section.html": "",
                    "partials/stats_counters.html": "",
                }),
                FileSystemLoader(str(tpl)),
            ]),
            autoescape=True,
        )
        return env.get_template("stats.html").render(
            days=30, stats={"days": 30}, demand={},
            gap={"days": 30, "total_combos": len(rows), "rows": rows},
        )

    @staticmethod
    def _fila(ptype="duplex", slug="casa-duplex", captar=True) -> dict:
        return {"city": "Asunción", "ptype": ptype, "demand": 10,
                "stock": 3, "captar": captar, "ptype_slug": slug}

    def test_captar_es_un_link_al_stock_de_ese_combo(self):
        html = self._render([self._fila()])
        assert 'href="/properties?city=Asunci%C3%B3n&amp;property_type=casa-duplex"' in html, (
            "el link de «Captar» no filtra por ciudad y por el slug del stock"
        )

    def test_el_link_no_manda_la_etiqueta_de_la_demanda(self):
        """'duplex' no es un property_type: mandarlo da una lista vacia."""
        html = self._render([self._fila()])
        assert "property_type=duplex" not in html, (
            "el link manda la etiqueta de la demanda en vez del slug contado"
        )

    def test_sin_slug_el_link_se_queda_con_la_ciudad(self):
        html = self._render([self._fila(slug=None)])
        assert 'href="/properties?city=Asunci%C3%B3n"' in html
        assert "property_type=" not in html

    def test_cubierto_no_es_link(self):
        """Solo «Captar» nombra un trabajo; «Cubierto» no lleva a ningun lado."""
        html = self._render([self._fila(captar=False)])
        assert "Cubierto" in html
        assert "/properties?city=" not in html

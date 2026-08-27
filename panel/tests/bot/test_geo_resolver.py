"""Tests for GeoResolver — geographic resolution for property search.

RED phase: all tests should fail against stubs.
"""
import os


import pytest

from app.bot.search.geo_resolver import (
    GeoData,
    GeoLocation,
    GeoResolver,
    LandmarkResult,
    ResolvedGeo,
    get_geo_data,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_geo_data_cache():
    """Reset the module-level GeoData singleton so each test class starts fresh."""
    import app.bot.search.geo_resolver as mod
    if hasattr(mod, "_geo_data_cache"):
        mod._geo_data_cache = None


# =========================================================================
# TestGeoData — loading JSON files
# =========================================================================

class TestGeoData:
    """Verify that get_geo_data() loads all geography files correctly."""

    def setup_method(self):
        _reset_geo_data_cache()

    def test_loads_all_files(self):
        """get_geo_data() returns object with non-empty cities, barrios, aliases, landmarks."""
        data = get_geo_data()
        assert isinstance(data, GeoData)
        assert len(data.cities) > 0, "cities should not be empty"
        assert len(data.barrios) > 0, "barrios should not be empty"
        assert len(data.aliases_cities) > 0, "city aliases should not be empty"
        assert len(data.aliases_barrios) > 0, "barrio aliases should not be empty"
        assert len(data.landmarks) > 0, "landmarks should not be empty"

    def test_singleton_same_instance(self):
        """get_geo_data() returns the same object on second call."""
        data1 = get_geo_data()
        data2 = get_geo_data()
        assert data1 is data2

    def test_city_count(self):
        """At least 36 cities loaded (Central + Cordillera + interior)."""
        data = get_geo_data()
        # ciudades_vecinas.json has 24 cities, ciudades_interior.json has 14 cities = 38 total
        assert len(data.cities) >= 36


# =========================================================================
# TestAliasResolution — alias lookups
# =========================================================================

class TestAliasResolution:
    """Verify alias resolution for cities and barrios."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        _reset_geo_data_cache()
        self.resolver = GeoResolver()

    def test_city_alias_asu(self):
        """'asu' resolves to 'asuncion'."""
        assert self.resolver.resolve_city_alias("asu") == "asuncion"

    def test_city_alias_cde(self):
        """'cde' resolves to 'ciudad del este'."""
        assert self.resolver.resolve_city_alias("cde") == "ciudad del este"

    def test_barrio_alias_carmelitas(self):
        """'carmelitas' resolves to 'las lomas'."""
        assert self.resolver.resolve_barrio_alias("carmelitas") == "las lomas"

    def test_barrio_alias_centro(self):
        """'centro' resolves to 'la catedral'."""
        assert self.resolver.resolve_barrio_alias("centro") == "la catedral"

    def test_unknown_alias_passthrough(self):
        """'villa morra' is not an alias — passes through unchanged."""
        assert self.resolver.resolve_barrio_alias("villa morra") == "villa morra"

    def test_accent_normalization(self):
        """'Asuncion' with accent normalizes to 'asuncion'."""
        # The resolver should normalize + lookup: "Asunción" -> "asuncion"
        result = self.resolver.resolve_city_alias("Asunción")
        assert result == "asuncion"


# =========================================================================
# TestCityNeighborExpansion — city neighbor graphs
# =========================================================================

class TestCityNeighborExpansion:
    """Verify city neighbor expansion with distance annotation."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        _reset_geo_data_cache()
        self.resolver = GeoResolver()

    def test_asuncion_neighbors(self):
        """Asuncion at distance 0, neighbors at distance 1, at least 5 neighbors."""
        locations = self.resolver.expand_city_neighbors("asuncion")
        names = {loc.name for loc in locations}
        distances = {loc.name: loc.distance for loc in locations}

        assert "asuncion" in names
        assert distances["asuncion"] == 0
        # From ciudades_vecinas.json: mariano roque alonso, luque, fernando de la mora, lambare, villa elisa
        for neighbor in ["mariano roque alonso", "luque", "fernando de la mora", "lambare", "villa elisa"]:
            assert neighbor in names, f"{neighbor} should be a neighbor of asuncion"
            assert distances[neighbor] == 1

    def test_unknown_city_returns_itself(self):
        """Unknown city returns only itself at distance 0."""
        locations = self.resolver.expand_city_neighbors("inventada")
        assert len(locations) == 1
        assert locations[0].name == "inventada"
        assert locations[0].distance == 0

    def test_interior_city(self):
        """'ciudad del este' has neighbors from ciudades_interior.json."""
        locations = self.resolver.expand_city_neighbors("ciudad del este")
        names = {loc.name for loc in locations}
        # From ciudades_interior.json: hernandarias, presidente franco, minga guazu
        assert "hernandarias" in names
        assert "presidente franco" in names
        assert "minga guazu" in names

    def test_expansion_returns_geo_locations(self):
        """Expansion results are GeoLocation instances with name and distance."""
        locations = self.resolver.expand_city_neighbors("asuncion")
        assert all(isinstance(loc, GeoLocation) for loc in locations)
        assert all(hasattr(loc, "name") and hasattr(loc, "distance") for loc in locations)


# =========================================================================
# TestBarrioNeighborExpansion — barrio neighbor graphs
# =========================================================================

class TestBarrioNeighborExpansion:
    """Verify barrio neighbor expansion within a city."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        _reset_geo_data_cache()
        self.resolver = GeoResolver()

    def test_villa_morra_neighbors(self):
        """Villa Morra in Asuncion has known neighbors."""
        locations = self.resolver.expand_barrio_neighbors("villa morra", "asuncion")
        names = {loc.name for loc in locations}
        # From barrios_asuncion_vecinos.json: bella vista, manora, mariscal estigarribia,
        # recoleta, san cristobal, santo domingo, ycua sati
        assert "villa morra" in names  # itself at distance 0
        assert "bella vista" in names
        assert "manora" in names
        assert "recoleta" in names

    def test_unknown_barrio_returns_itself(self):
        """Unknown barrio returns only itself at distance 0."""
        locations = self.resolver.expand_barrio_neighbors("inventado", "asuncion")
        assert len(locations) == 1
        assert locations[0].name == "inventado"
        assert locations[0].distance == 0

    def test_barrio_in_luque(self):
        """A barrio in Luque has neighbors from barrios_luque_vecinos.json."""
        locations = self.resolver.expand_barrio_neighbors("campo grande", "luque")
        names = {loc.name for loc in locations}
        # From barrios_luque_vecinos.json: primer barrio, loma merlo, zarate isla, nu guasu, ykua karanda'y, nueva asuncion
        assert "campo grande" in names
        assert "loma merlo" in names
        assert "nu guasu" in names

    def test_city_without_barrio_data(self):
        """City without barrio file returns just the barrio at distance 0."""
        # encarnacion has no barrios_encarnacion_vecinos.json
        locations = self.resolver.expand_barrio_neighbors("centro", "encarnacion")
        assert len(locations) == 1
        assert locations[0].name == "centro"
        assert locations[0].distance == 0


# =========================================================================
# TestLandmarkResolution — landmark lookups
# =========================================================================

class TestLandmarkResolution:
    """Verify landmark resolution to barrio+city."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        _reset_geo_data_cache()
        self.resolver = GeoResolver()

    def test_landmark_paseo_galeria(self):
        """'paseo la galeria' resolves to villa morra, Asuncion."""
        result = self.resolver.resolve_landmark("paseo la galeria")
        assert result is not None
        assert isinstance(result, LandmarkResult)
        assert result.barrio == "villa morra"
        assert result.ciudad == "asuncion"

    def test_landmark_alias(self):
        """'galeria' alias resolves to paseo la galeria landmark."""
        result = self.resolver.resolve_landmark("galeria")
        assert result is not None
        assert result.barrio == "villa morra"
        assert result.ciudad == "asuncion"

    def test_landmark_aeropuerto(self):
        """'aeropuerto' resolves to Luque from landmarks_central.json."""
        result = self.resolver.resolve_landmark("aeropuerto")
        assert result is not None
        assert result.ciudad == "luque"
        assert result.barrio == "nu guasu"
        assert "campo grande" in result.barrios_cercanos

    def test_unknown_landmark(self):
        """Unknown text returns None."""
        result = self.resolver.resolve_landmark("lugar_inexistente_xyz")
        assert result is None

    def test_landmark_shopping_del_sol(self):
        """'shopping del sol' resolves to manora, Asuncion (corrected 2026-04-26)."""
        result = self.resolver.resolve_landmark("shopping del sol")
        assert result is not None
        assert result.ciudad == "asuncion"
        assert result.barrio == "manora"

    def test_landmark_shopping_del_sol_alias_del_sol(self):
        """'del sol' alias resolves to Shopping del Sol → manora, Asuncion."""
        result = self.resolver.resolve_landmark("del sol")
        assert result is not None
        assert result.ciudad == "asuncion"
        assert result.barrio == "manora"

    def test_landmark_shopping_del_sol_alias_mall_del_sol(self):
        """'mall del sol' alias resolves to Shopping del Sol → manora, Asuncion."""
        result = self.resolver.resolve_landmark("mall del sol")
        assert result is not None
        assert result.ciudad == "asuncion"
        assert result.barrio == "manora"

    def test_landmark_shopping_multiplaza(self):
        """'shopping multiplaza' resolves to mburucuya, Asuncion."""
        result = self.resolver.resolve_landmark("shopping multiplaza")
        assert result is not None
        assert result.ciudad == "asuncion"
        assert result.barrio == "mburucuya"


# =========================================================================
# TestInterCityAdjacency — cross-city border barrios
# =========================================================================

class TestInterCityAdjacency:
    """Verify inter-city barrio adjacency lookups."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        _reset_geo_data_cache()
        self.resolver = GeoResolver()

    def test_cross_city_barrios(self):
        """Luque-Asuncion border barrios from inter_city_adjacency.json."""
        barrios = self.resolver.get_cross_city_barrios("luque", "asuncion")
        # First adjacency entry: luque [nu guasu, campo grande] <-> asuncion [loma pyta, zeballos cue]
        # The method should return barrios in city_b (asuncion) that border city_a (luque)
        assert len(barrios) > 0
        # Barrios in asuncion that border luque
        assert "loma pyta" in barrios or "zeballos cue" in barrios

    def test_bidirectional(self):
        """Reversed lookup (asuncion->luque) also works."""
        barrios_fwd = self.resolver.get_cross_city_barrios("luque", "asuncion")
        barrios_rev = self.resolver.get_cross_city_barrios("asuncion", "luque")
        # Both directions should return non-empty results
        assert len(barrios_fwd) > 0
        assert len(barrios_rev) > 0

    def test_no_border_barrio(self):
        """Cities with no shared border return empty list."""
        barrios = self.resolver.get_cross_city_barrios("encarnacion", "asuncion")
        assert barrios == []


# =========================================================================
# TestResolveMethod — full resolution pipeline
# =========================================================================

class TestResolveMethod:
    """Verify the full resolve() pipeline combining aliases + expansion."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        _reset_geo_data_cache()
        self.resolver = GeoResolver()

    def test_resolve_city_only(self):
        """Resolve with city only: city_locations populated, barrio_locations empty."""
        result = self.resolver.resolve(city="asuncion")
        assert isinstance(result, ResolvedGeo)
        assert result.canonical_city == "asuncion"
        assert len(result.city_locations) > 0
        assert len(result.barrio_locations) == 0

    def test_resolve_city_and_barrio(self):
        """Resolve with city and barrio: both populated."""
        result = self.resolver.resolve(city="asuncion", barrios=["villa morra"])
        assert result.canonical_city == "asuncion"
        assert len(result.city_locations) > 0
        assert len(result.barrio_locations) > 0
        barrio_names = {loc.name for loc in result.barrio_locations}
        assert "villa morra" in barrio_names

    def test_resolve_with_alias(self):
        """Aliases are resolved before expansion: 'asu' -> 'asuncion'."""
        result = self.resolver.resolve(city="asu")
        assert result.canonical_city == "asuncion"
        city_names = {loc.name for loc in result.city_locations}
        assert "asuncion" in city_names

    def test_resolve_multiple_barrios(self):
        """Multiple barrios produce merged barrio_locations."""
        result = self.resolver.resolve(city="asuncion", barrios=["villa morra", "recoleta"])
        barrio_names = {loc.name for loc in result.barrio_locations}
        assert "villa morra" in barrio_names
        assert "recoleta" in barrio_names

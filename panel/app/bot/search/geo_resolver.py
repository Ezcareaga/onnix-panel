"""GeoResolver — geographic normalization and neighbor expansion for property search.

Resolves city/barrio aliases, expands neighbors by distance, and resolves landmarks
to barrio+city pairs using the JSON data in data/geografia/.
"""
from __future__ import annotations

import json
import logging
import os
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class GeoLocation:
    """A geographic location with a distance from the original query."""
    name: str
    distance: int


@dataclass
class LandmarkResult:
    """Result of resolving a landmark to its geographic location."""
    ciudad: str
    barrio: str
    barrios_cercanos: list[str]


@dataclass
class ResolvedGeo:
    """Full result of geographic resolution for a search query."""
    canonical_city: str | None
    city_locations: list[GeoLocation] = field(default_factory=list)
    barrio_locations: list[GeoLocation] = field(default_factory=list)
    landmark: LandmarkResult | None = None


# ---------------------------------------------------------------------------
# GeoData container + singleton
# ---------------------------------------------------------------------------

class GeoData:
    """Container for all loaded geographic data."""

    def __init__(self) -> None:
        # city_name -> list[neighbor_names]
        self.cities: dict[str, list[str]] = {}
        # city_name -> { barrio_name -> list[neighbor_barrio_names] }
        self.barrios: dict[str, dict[str, list[str]]] = {}
        # alias -> canonical city name
        self.aliases_cities: dict[str, str] = {}
        # alias -> canonical barrio name
        self.aliases_barrios: dict[str, str] = {}
        # flat dict: landmark_key_or_alias -> { ciudad, barrio, barrios_cercanos, ... }
        self.landmarks: dict[str, dict] = {}
        # list of adjacency records from inter_city_adjacency.json
        self.inter_city: list[dict] = []


# Module-level singleton cache
_geo_data_cache: GeoData | None = None


def _normalize(text: str) -> str:
    """Lowercase + NFKD decomposition + strip combining characters."""
    text = text.lower().strip()
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def _load_json(path: Path) -> dict | None:
    """Load a JSON file, returning None on error."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("Geo data file not found: %s", path)
        return None
    except json.JSONDecodeError as exc:
        logger.warning("Invalid JSON in %s: %s", path, exc)
        return None


def get_geo_data() -> GeoData:
    """Load and cache geographic data from JSON files.

    Returns the same GeoData instance on subsequent calls (singleton).
    Data path comes from GEO_DATA_PATH env var, falling back to
    <raíz del repo>/data/geografia.
    """
    global _geo_data_cache
    if _geo_data_cache is not None:
        return _geo_data_cache

    # Default relativo a la raíz del repo: la ruta absoluta del VPS dejó de existir
    # cuando el código salió del home (2026-08-18). En el contenedor manda
    # GEO_DATA_PATH=/app/data/geografia, que setea el compose.
    _default_geo = Path(__file__).resolve().parents[4] / "data" / "geografia"
    base = Path(os.environ.get("GEO_DATA_PATH", str(_default_geo)))
    data = GeoData()

    # ------------------------------------------------------------------
    # 1. Cities: ciudades_vecinas.json + ciudades_interior.json
    # ------------------------------------------------------------------
    for fname in ("ciudades_vecinas.json", "ciudades_interior.json"):
        raw = _load_json(base / fname)
        if raw and "ciudades" in raw:
            for key, info in raw["ciudades"].items():
                nkey = _normalize(key)
                data.cities[nkey] = [_normalize(v) for v in info.get("vecinas", [])]

    # ------------------------------------------------------------------
    # 2. Barrios: 6 barrios_*_vecinos.json files
    # ------------------------------------------------------------------
    barrio_files = {
        "barrios_asuncion_vecinos.json": "asuncion",
        "barrios_luque_vecinos.json": "luque",
        "barrios_fdm_vecinos.json": "fernando de la mora",
        "barrios_lambare_vecinos.json": "lambare",
        "barrios_mra_vecinos.json": "mariano roque alonso",
        "barrios_san_lorenzo_vecinos.json": "san lorenzo",
    }
    for fname, city_key in barrio_files.items():
        raw = _load_json(base / fname)
        if raw and "barrios" in raw:
            city_barrios: dict[str, list[str]] = {}
            for bkey, binfo in raw["barrios"].items():
                nbkey = _normalize(bkey)
                city_barrios[nbkey] = [_normalize(v) for v in binfo.get("vecinos", [])]
            data.barrios[_normalize(city_key)] = city_barrios

    # ------------------------------------------------------------------
    # 3. Aliases from aliases.json
    # ------------------------------------------------------------------
    raw = _load_json(base / "aliases.json")
    if raw:
        for alias, canonical in raw.get("ciudades", {}).items():
            data.aliases_cities[_normalize(alias)] = _normalize(canonical)
        for alias, canonical in raw.get("barrios", {}).items():
            data.aliases_barrios[_normalize(alias)] = _normalize(canonical)

    # ------------------------------------------------------------------
    # 4. Landmarks from 3 files
    # ------------------------------------------------------------------
    # landmarks_asuncion.json has a nested structure: { landmarks: { category: { name: {...} } } }
    raw = _load_json(base / "landmarks_asuncion.json")
    if raw and "landmarks" in raw:
        for _category, entries in raw["landmarks"].items():
            for lname, linfo in entries.items():
                nlname = _normalize(lname)
                record = {
                    "ciudad": "asuncion",
                    "barrio": _normalize(linfo["barrio"]) if linfo.get("barrio") else None,
                    "barrios_cercanos": [_normalize(b) for b in linfo.get("barrios_cercanos", [])],
                }
                data.landmarks[nlname] = record
                for alias in linfo.get("alias", []):
                    data.landmarks[_normalize(alias)] = record

    # landmarks_central.json and landmarks_interior.json have flat structure:
    # { landmarks: { name: { ciudad, barrio, barrios_cercanos, alias } } }
    for fname in ("landmarks_central.json", "landmarks_interior.json"):
        raw = _load_json(base / fname)
        if raw and "landmarks" in raw:
            for lname, linfo in raw["landmarks"].items():
                nlname = _normalize(lname)
                record = {
                    "ciudad": _normalize(linfo["ciudad"]),
                    "barrio": _normalize(linfo["barrio"]) if linfo.get("barrio") else None,
                    "barrios_cercanos": [_normalize(b) for b in linfo.get("barrios_cercanos", [])],
                }
                data.landmarks[nlname] = record
                for alias in linfo.get("alias", []):
                    data.landmarks[_normalize(alias)] = record

    # ------------------------------------------------------------------
    # 5. Inter-city adjacency
    # ------------------------------------------------------------------
    raw = _load_json(base / "inter_city_adjacency.json")
    if raw and "adjacencies" in raw:
        data.inter_city = raw["adjacencies"]

    _geo_data_cache = data
    return data


# ---------------------------------------------------------------------------
# GeoResolver
# ---------------------------------------------------------------------------

class GeoResolver:
    """Resolves geographic queries using loaded data."""

    def __init__(self, geo_data: GeoData | None = None) -> None:
        self.data = geo_data or get_geo_data()

    def normalize(self, text: str) -> str:
        """Normalize text: lowercase, strip accents."""
        return _normalize(text)

    def resolve_city_alias(self, city: str) -> str:
        """Resolve a city alias to its canonical name.

        If the normalized city is in the alias table, return the canonical.
        Otherwise return the normalized name as-is.
        """
        normed = _normalize(city)
        return self.data.aliases_cities.get(normed, normed)

    def is_known_city(self, name: str) -> bool:
        """Return True if *name* resolves to a city in the city adjacency data."""
        canonical = self.resolve_city_alias(name)
        return canonical in self.data.cities

    def resolve_barrio_alias(self, barrio: str) -> str:
        """Resolve a barrio alias to its canonical name.

        If the normalized barrio is in the alias table, return the canonical.
        Otherwise return the normalized name as-is.
        """
        normed = _normalize(barrio)
        return self.data.aliases_barrios.get(normed, normed)

    def expand_city_neighbors(self, city: str, max_distance: int = 1) -> list[GeoLocation]:
        """Expand a city to include its neighbors up to max_distance.

        Returns the city itself at distance 0, direct neighbors at distance 1, etc.
        If the city is not in the data, returns just the city at distance 0.
        """
        normed = _normalize(city)
        if normed not in self.data.cities:
            return [GeoLocation(name=normed, distance=0)]

        # BFS expansion
        visited: dict[str, int] = {normed: 0}
        frontier = [normed]
        for dist in range(1, max_distance + 1):
            next_frontier: list[str] = []
            for c in frontier:
                for neighbor in self.data.cities.get(c, []):
                    if neighbor not in visited:
                        visited[neighbor] = dist
                        next_frontier.append(neighbor)
            frontier = next_frontier

        return [GeoLocation(name=name, distance=d) for name, d in visited.items()]

    def expand_barrio_neighbors(self, barrio: str, city: str) -> list[GeoLocation]:
        """Expand a barrio to include its neighbors within a city.

        Returns the barrio at distance 0, direct neighbors at distance 1.
        If the city has no barrio data, or the barrio is unknown, returns
        just the barrio at distance 0.
        """
        normed_barrio = _normalize(barrio)
        normed_city = _normalize(city)

        city_barrios = self.data.barrios.get(normed_city)
        if not city_barrios or normed_barrio not in city_barrios:
            return [GeoLocation(name=normed_barrio, distance=0)]

        neighbors = city_barrios[normed_barrio]
        result = [GeoLocation(name=normed_barrio, distance=0)]
        for nb in neighbors:
            result.append(GeoLocation(name=nb, distance=1))
        return result

    def resolve_landmark(self, text: str) -> LandmarkResult | None:
        """Try to resolve text as a landmark.

        Looks up the normalized text in the landmark table (which includes
        both canonical names and aliases). Returns None if not found.
        """
        normed = _normalize(text)
        record = self.data.landmarks.get(normed)
        if record is None:
            return None
        return LandmarkResult(
            ciudad=record["ciudad"],
            barrio=record["barrio"] or "",
            barrios_cercanos=record.get("barrios_cercanos", []),
        )

    def get_cross_city_barrios(self, city_a: str, city_b: str) -> list[str]:
        """Get border barrios in city_b that are adjacent to city_a.

        Searches inter_city_adjacency data bidirectionally.
        Returns list of barrio names in city_b.
        """
        na = _normalize(city_a)
        nb = _normalize(city_b)
        result: list[str] = []

        for adj in self.data.inter_city:
            ca = _normalize(adj.get("city_a", ""))
            cb = _normalize(adj.get("city_b", ""))
            if ca == na and cb == nb:
                result.extend(_normalize(b) for b in adj.get("barrios_b", []))
            elif ca == nb and cb == na:
                result.extend(_normalize(b) for b in adj.get("barrios_a", []))

        return result

    def resolve(
        self,
        city: str | None = None,
        barrios: list[str] | None = None,
    ) -> ResolvedGeo:
        """Full resolution: aliases + neighbor expansion for city and barrios.

        1. Resolve city alias
        2. Expand city neighbors
        3. For each barrio: resolve alias, expand barrio neighbors
        4. Deduplicate barrio locations
        """
        canonical_city: str | None = None
        city_locations: list[GeoLocation] = []
        barrio_locations: list[GeoLocation] = []

        if city:
            canonical_city = self.resolve_city_alias(city)
            # Don't expand to neighbor cities in initial search — only the
            # exact city.  Neighbor expansion happens in relaxation (Level 3)
            # when the initial query returns zero results.
            city_locations = [GeoLocation(name=canonical_city, distance=0)]

        if barrios:
            seen_barrios: set[str] = set()
            for b in barrios:
                resolved_b = self.resolve_barrio_alias(b)
                expanded = self.expand_barrio_neighbors(resolved_b, canonical_city or "")
                for loc in expanded:
                    if loc.name not in seen_barrios:
                        seen_barrios.add(loc.name)
                        barrio_locations.append(loc)

        return ResolvedGeo(
            canonical_city=canonical_city,
            city_locations=city_locations,
            barrio_locations=barrio_locations,
        )

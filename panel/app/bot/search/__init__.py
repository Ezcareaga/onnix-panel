"""Bot search package — property search pipeline components.

Exports all public symbols from sub-modules for convenient imports:

    from app.bot.search import SearchService, SearchFilters, SearchResult
"""
from .geo_resolver import (
    GeoData,
    GeoLocation,
    GeoResolver,
    LandmarkResult,
    ResolvedGeo,
    get_geo_data,
)
from .hybrid_search import reciprocal_rank_fusion
from .relaxation import (
    DegradationInfo,
    FilterRelaxation,
    RelaxationResult,
)
from .search_service import SearchResult, SearchService
from .sql_filters import FilteredQuery, SQLFilterBuilder, SearchFilters
from .vector_search import VectorSearch

__all__ = [
    # geo_resolver
    "GeoData",
    "GeoLocation",
    "GeoResolver",
    "LandmarkResult",
    "ResolvedGeo",
    "get_geo_data",
    # hybrid_search
    "reciprocal_rank_fusion",
    # relaxation
    "DegradationInfo",
    "FilterRelaxation",
    "RelaxationResult",
    # search_service
    "SearchResult",
    "SearchService",
    # sql_filters
    "FilteredQuery",
    "SQLFilterBuilder",
    "SearchFilters",
    # vector_search
    "VectorSearch",
]

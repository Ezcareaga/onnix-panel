from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import Text, bindparam, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import Property
from app.models.infocasas_property import InfocasasProperty
from app.utils.amenities import normalize_amenity

_LIST_COLUMNS = (
    "id, source, external_id, title, url, price_usd, price_pyg, price_currency,"
    " city, neighborhood, operation, property_type, bedrooms, bathrooms,"
    " total_area_m2, construction_state, is_active, on_hold, updated_at,"
    " created_at, portal_listed_at, portal_expires_at, main_image_url,"
    " local_image_count"
)


def _build_filter_sql(filters: "PropertyFilters") -> tuple[str, dict]:  # type: ignore[name-defined]
    """Build parameterized WHERE clauses and params dict from PropertyFilters.

    Always includes duplicate_of IS NULL (callers never see dupes).
    State logic:
      active   → is_active = TRUE  AND on_hold = FALSE
      on_hold  → is_active = TRUE  AND on_hold = TRUE
      inactive → is_active = FALSE
      all      → no is_active/on_hold filter
      None     → treated as active
    """
    clauses = ["duplicate_of IS NULL"]
    params: dict = {}

    state = filters.state if filters.state is not None else "active"
    if state == "active":
        clauses.append("is_active = TRUE AND on_hold = FALSE")
    elif state == "on_hold":
        clauses.append("is_active = TRUE AND on_hold = TRUE")
    elif state == "inactive":
        clauses.append("is_active = FALSE")
    # state == "all" → no filter

    if filters.property_type:
        clauses.append("property_type = :property_type")
        params["property_type"] = filters.property_type

    if filters.operation:
        clauses.append("operation = :operation")
        params["operation"] = filters.operation

    if filters.city:
        clauses.append("unaccent(city) ILIKE unaccent(:city)")
        params["city"] = f"%{filters.city}%"

    if filters.neighborhood:
        clauses.append("unaccent(neighborhood) ILIKE unaccent(:neighborhood)")
        params["neighborhood"] = f"%{filters.neighborhood}%"

    # La moneda dice EN QUÉ está escrito el número del rango, no qué etiqueta
    # tiene la propiedad. Antes eran dos cláusulas que se contradecían: el rango
    # comparaba siempre contra `price_usd` y `price_currency = :currency`
    # filtraba por la etiqueta. Medido en producción el 2026-08-24 sobre las
    # 14.033 activas no duplicadas, 6.747 tienen etiqueta 'PYG' **y** `price_usd`
    # cargado — el precio en dólares estaba listo y la fila se descartaba igual
    # («casa 3 dorm Lambaré ≤150k» daba 6 en vez de 42). El camino PYG era peor:
    # Claude emite guaraníes crudos (350000000) y eso comparado contra
    # `price_usd` no filtraba nada (120 filas contra 62).
    # Mismo criterio que el bot desde siempre: bot/search/sql_filters._price_column.
    price_col = "price_pyg" if (filters.currency or "").upper() == "PYG" else "price_usd"

    if filters.price_min is not None:
        clauses.append(f"{price_col} >= :price_min")
        params["price_min"] = filters.price_min

    if filters.price_max is not None:
        clauses.append(f"{price_col} <= :price_max")
        params["price_max"] = filters.price_max

    if filters.bedrooms_min is not None:
        clauses.append("bedrooms >= :bedrooms_min")
        params["bedrooms_min"] = filters.bedrooms_min

    if filters.bathrooms_min is not None:
        clauses.append("bathrooms >= :bathrooms_min")
        params["bathrooms_min"] = filters.bathrooms_min

    if filters.source:
        clauses.append("source = :source")
        params["source"] = filters.source

    if filters.construction_state:
        clauses.append("construction_state = :construction_state")
        params["construction_state"] = filters.construction_state

    if filters.updated_within_days is not None:
        # make_interval binds the param properly; INTERVAL ':x days' put the
        # bindparam inside a string literal and never bound it (bug fix M6.5).
        clauses.append(
            "updated_at >= NOW() - make_interval(days => :updated_within_days)"
        )
        params["updated_within_days"] = filters.updated_within_days

    if filters.amenities:
        # Defense in depth: re-validate against the canonical whitelist even
        # though the route/parser already normalizes. Invalid values are
        # silently dropped — never interpolated into SQL.
        idx = 0
        for raw in filters.amenities:
            canonical = normalize_amenity(raw)
            if canonical is None:
                continue
            key = f"amenity_{idx}"
            clauses.append(
                f"unaccent(lower(description)) ILIKE unaccent(:{key})"
            )
            params[key] = f"%{canonical}%"
            idx += 1

    if filters.search_text:
        # Match on title (unaccent for Spanish) OR external_id (raw — IDs are alnum/hyphen).
        # Lets la administradora paste an external_id directly into the free-text filter.
        clauses.append(
            "(unaccent(title) ILIKE unaccent(:search_text)"
            " OR external_id ILIKE :search_text)"
        )
        params["search_text"] = f"%{filters.search_text}%"

    return " AND ".join(clauses), params


def _barato_cte(filters: "PropertyFilters") -> tuple[str, str, dict]:  # type: ignore[name-defined]
    """Build the P25 CTE for the 'barato' filter (M6.5).

    Returns (cte_sql, extra_where, params). The CTE computes the 25th price
    percentile over active properties, scoped to property_type/city/operation
    when those filters are present (local P25) or globally per operation
    otherwise. extra_where caps results via a scalar subquery so list and
    count queries apply the exact same cap (consistent totals).

    The CAST(:param AS text) in the NULL checks gives asyncpg an explicit
    type for None params (same pattern as app/bot/search/sql_filters.py).
    """
    cte_sql = (
        "WITH p25 AS ("
        " SELECT PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price_usd) AS v"
        " FROM properties"
        " WHERE is_active = TRUE AND price_usd IS NOT NULL"
        "   AND (CAST(:p25_type AS text) IS NULL OR property_type = :p25_type)"
        "   AND (CAST(:p25_city AS text) IS NULL"
        "        OR unaccent(city) ILIKE unaccent(:p25_city))"
        "   AND (CAST(:p25_operation AS text) IS NULL"
        "        OR operation = :p25_operation)"
        ")"
    )
    params = {
        "p25_type": filters.property_type or None,
        "p25_city": f"%{filters.city}%" if filters.city else None,
        "p25_operation": filters.operation or None,
    }
    return cte_sql, "price_usd <= (SELECT v FROM p25)", params


def _apply_barato(
    filters: "PropertyFilters",  # type: ignore[name-defined]
    where: str,
    params: dict,
) -> tuple[str, str]:
    """Return (cte_prefix, where) with the barato cap applied when set.

    Shared by list_with_filters and count_with_filters so both queries are
    guaranteed identical (no drift between rows shown and total count).
    Mutates params in place to add the p25_* bindings.
    """
    if not filters.barato:
        return "", where
    cte_sql, extra_where, cte_params = _barato_cte(filters)
    params.update(cte_params)
    return f"{cte_sql} ", f"{where} AND {extra_where}"


class StockCombo(NamedTuple):
    """Stock activo de un combo (ciudad, tipo) y el slug con el que se conto."""

    stock: int
    slug: str | None


# Las propiedades sin foto van al final.
#
# Pedido de Ez el 2026-08-24: «al menos que no aparezcan primerito». No se
# ocultan —la ficha tiene un estado sin-foto hecho a proposito, que pide las
# fotos por WhatsApp y captura el lead igual— pero dejan de encabezar el
# listado. Son 89 de 19.941 activas: 88 tienen la URL y el token del CDN vencio,
# y una nunca tuvo.
#
# `false` ordena antes que `true` en Postgres, asi que `(... = 0) ASC` pone
# primero a las que tienen foto sin invertir nada de lo que venia despues.
#
# Vive en una sola funcion porque `list_with_filters` y `list_ids_with_filters`
# tienen que ordenar IGUAL: la segunda es la pata SQL de la fusion RRF y su
# propio docstring dice «identical ORDER BY». Estaban escritas dos veces, que es
# como empiezan a divergir.
_SIN_FOTO_AL_FINAL = "(COALESCE(local_image_count, 0) = 0) ASC"


# El portal público colapsa el proyecto en una tarjeta.
#
# Medido el 2026-08-24 sobre las 5.105 listables de onnixpy —lo único que ve
# `/propiedades`—: **407 títulos producen 2.112 filas, el 41,4 % del portal**. El
# peor reparte 85 tarjetas del mismo barrio cerrado, con la misma foto y el mismo
# texto, que para quien mira es una sola opción.
#
# No son duplicados: `run_dedup_same_source` ya se llevó las 2.495 que sí lo eran
# (título, precio y superficie idénticos). Éstas difieren en precio o superficie
# —son lotes o unidades distintas del mismo proyecto— y el dato es correcto. Lo
# que estaba mal era mostrarlas de a una.
#
# Es el mismo arreglo que la landing ya tenía, y su comentario lo dice: «sin el
# DISTINCT ON, las seis destacadas salían siendo seis terrenos del mismo country
# club» (`scripts/build_destacadas.py:57`). El listado nunca lo recibió.
#
# Se aplica SÓLO al portal. El panel sigue viendo cada unidad: el asesor tiene
# que poder mandarle al cliente el lote 47, no «el proyecto».
_COLUMNAS_PROYECTO = (
    " , count(*) OVER (PARTITION BY source, title) AS unidades"
    " , min(price_usd) OVER (PARTITION BY source, title) AS precio_desde"
)

# Cuál de las N filas representa al proyecto: la que mejor se ve —más fotos— y
# entre ésas la más barata, que es el «desde» que muestra la tarjeta.
_REPRESENTANTE = (
    "source, title, COALESCE(local_image_count, 0) DESC,"
    " price_usd ASC NULLS LAST, id"
)


def _orden_listado(state: str | None) -> str:
    """El ORDER BY del listado de propiedades, panel y portal publico.

    El portal usa `list_with_filters` igual que el panel, asi que este es el
    unico lugar donde se decide el orden de las dos superficies.
    """
    reciente = "updated_at DESC" if (state or "active") == "inactive" else "created_at DESC"
    return f"{_SIN_FOTO_AL_FINAL}, {reciente}"


class PropertyRepository:
    @staticmethod
    async def search(db: AsyncSession, q: str) -> list[dict]:
        """Full-text search on external_id or title for active properties.

        Uses ILIKE for case-insensitive partial matching.  unaccent() is NOT
        applied here because external_id values are numeric codes, and title
        searches benefit more from broad ILIKE than strict unaccent folding.
        Returns at most 6 results with minimal display fields, newest first.
        """
        result = await db.execute(
            text(
                "SELECT id, external_id, title, city, neighborhood, price_usd"
                " FROM properties"
                " WHERE is_active = TRUE"
                "   AND (external_id ILIKE :q OR title ILIKE :q)"
                " ORDER BY id DESC"
                " LIMIT 6"
            ),
            {"q": f"%{q}%"},
        )
        rows = result.mappings().all()
        return [
            {
                "id": row["id"],
                "external_id": row["external_id"] or "",
                "title": row["title"] or "",
                "city": row["city"] or "",
                "neighborhood": row["neighborhood"] or "",
                "price_usd": float(row["price_usd"]) if row["price_usd"] is not None else None,
            }
            for row in rows
        ]

    @staticmethod
    async def get_by_id(db: AsyncSession, property_id: int) -> Property | None:
        result = await db.execute(select(Property).where(Property.id == property_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_ids(db: AsyncSession, property_ids: list[int]) -> dict[int, Property]:
        if not property_ids:
            return {}
        result = await db.execute(select(Property).where(Property.id.in_(property_ids)))
        return {p.id: p for p in result.scalars().all()}

    @staticmethod
    async def get_summary_by_ids(db: AsyncSession, ids: list[int]) -> list[dict]:
        """Fetch minimal property data for conversation thread display.

        Returns dicts with display-ready fields. Image URL prefers the first
        locally-cached WebP if local_image_count > 0, otherwise falls back to
        main_image_url from the scraper.
        """
        if not ids:
            return []
        result = await db.execute(
            text(
                "SELECT id, title, price_usd, price_currency, city, neighborhood,"
                " operation, property_type, bedrooms, bathrooms, total_area_m2,"
                " source, external_id, local_image_count, main_image_url, url"
                " FROM properties"
                " WHERE id = ANY(:ids)"
            ),
            {"ids": list(ids)},
        )
        rows = result.mappings().all()
        summaries = []
        for row in rows:
            if row["local_image_count"] and row["local_image_count"] > 0:
                image_url = url_foto(row["source"], row["external_id"])
            else:
                image_url = row["main_image_url"]
            summaries.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "price_usd": row["price_usd"],
                    "price_currency": row["price_currency"],
                    "city": row["city"],
                    "neighborhood": row["neighborhood"],
                    "operation": row["operation"],
                    "property_type": row["property_type"],
                    "bedrooms": row["bedrooms"],
                    "bathrooms": row["bathrooms"],
                    "total_area_m2": row["total_area_m2"],
                    "source": row["source"],
                    "external_id": row["external_id"],
                    "local_image_count": row["local_image_count"],
                    "image_url": image_url,
                    "url": row["url"] or "",
                }
            )
        return summaries

    @staticmethod
    async def get_by_source_external_id(
        db: AsyncSession, source: str, external_id: str
    ) -> "Property | None":
        """Get a property by its (source, external_id) composite key.

        Used to resolve Onnix property links, e.g.
        ``onnix.com.py/propiedad/39711`` → source='onnixpy', external_id='39711'.
        """
        result = await db.execute(
            select(Property).where(
                Property.source == source,
                Property.external_id == external_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_ic_by_id(db: AsyncSession, id: int) -> InfocasasProperty | None:
        """Get an InfoCasas property by its primary key.

        Used by handlers.detail_ic.handle_ver_detalles_ic to fetch IC property
        data directly, bypassing the cross-reference to the properties table.
        """
        result = await db.execute(
            select(InfocasasProperty).where(InfocasasProperty.id == id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_ic_by_infocasas_id(
        db: AsyncSession, infocasas_id: str
    ) -> InfocasasProperty | None:
        """Get InfoCasas property by infocasas_id (numeric URL segment).

        Used to resolve property links shared by users, e.g.
        ``www.infocasas.com.py/slug/189190235`` → infocasas_id='189190235'.
        """
        if not infocasas_id:
            return None
        result = await db.execute(
            select(InfocasasProperty).where(
                InfocasasProperty.infocasas_id == infocasas_id,
                InfocasasProperty.is_active == True,  # noqa: E712
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_ic_by_ref(db: AsyncSession, ref: str) -> InfocasasProperty | None:
        """Get InfoCasas property by infocasas_ref code."""
        if not ref:
            return None
        result = await db.execute(
            select(InfocasasProperty).where(
                InfocasasProperty.infocasas_ref == ref,
                InfocasasProperty.is_active == True,  # noqa: E712
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_ic_by_refs(db: AsyncSession, refs: list[str]) -> dict[str, InfocasasProperty]:
        """Get multiple InfoCasas properties by ref codes. Returns {ref: property}."""
        if not refs:
            return {}
        result = await db.execute(
            select(InfocasasProperty).where(
                InfocasasProperty.infocasas_ref.in_(refs),
                InfocasasProperty.is_active == True,  # noqa: E712
            )
        )
        return {p.infocasas_ref: p for p in result.scalars().all()}


    @staticmethod
    async def set_active(db: AsyncSession, property_id: int, value: bool) -> bool:
        """Set is_active for a property. Returns True if a row was updated."""
        result = await db.execute(
            text(
                "UPDATE properties SET is_active = :v, updated_at = NOW()"
                " WHERE id = :id"
            ),
            {"v": value, "id": property_id},
        )
        await db.flush()
        return (result.rowcount or 0) > 0

    @staticmethod
    async def get_full_detail(db: AsyncSession, property_id: int) -> dict | None:
        """Fetch all columns needed for the property detail view."""
        result = await db.execute(
            text(
                "SELECT id, source, external_id, title, url,"
                " price_usd, price_pyg, price_currency,"
                " operation, property_type,"
                " city, neighborhood,"
                " bedrooms, bathrooms, parking_spaces AS parking, total_area_m2,"
                " construction_state, description,"
                " agent_name, agent_phone, agent_whatsapp,"
                " is_active, on_hold,"
                " local_image_count, main_image_url,"
                " latitude, longitude,"
                " created_at, updated_at, last_scraped_at,"
                " portal_listed_at, portal_expires_at"
                " FROM properties"
                " WHERE id = :id"
            ),
            {"id": property_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None

    @staticmethod
    async def get_public_sitemap_rows(
        db: AsyncSession, sources: Sequence[str]
    ) -> list[dict]:
        """Return minimal rows for public sitemap generation.

        Fetches only the fields needed to build sitemap <loc> and <lastmod>
        entries. Filters to active, non-on_hold properties whose source is in
        the provided whitelist. Ordered by id for stable pagination.
        """
        if not sources:
            return []
        result = await db.execute(
            text(
                # property_type y operation no son para el XML: son para que
                # `_slug_publico` componga el MISMO slug que la ficha cuando el
                # titulo no deja nada. Sin ellas el sitemap listaria URLs que
                # redirigen.
                "SELECT id, title, city, property_type, operation, updated_at"
                " FROM properties"
                " WHERE is_active IS TRUE"
                "   AND on_hold IS NOT TRUE"
                "   AND source = ANY(:sources)"
                " ORDER BY id"
            ),
            {"sources": list(sources)},
        )
        return [dict(row) for row in result.mappings().all()]

    @staticmethod
    async def list_with_filters(
        db: AsyncSession,
        filters: "PropertyFilters",  # type: ignore[name-defined]
        limit: int = 50,
        offset: int = 0,
        colapsar_proyectos: bool = False,
    ) -> list[dict]:
        """Return paginated properties matching filters.

        Sorts by updated_at DESC for inactive state, created_at DESC otherwise.
        Never returns duplicate rows (duplicate_of IS NULL always applied).

        `colapsar_proyectos` devuelve UNA fila por `(source, title)`, con
        `unidades` y `precio_desde`. Lo usa sólo el portal público — ver
        `_COLUMNAS_PROYECTO`. El panel y el bot lo dejan en False y ven cada
        unidad.
        """
        where, params = _build_filter_sql(filters)
        cte_prefix, where = _apply_barato(filters, where, params)
        order = _orden_listado(filters.state)
        params["limit"] = limit
        params["offset"] = offset
        if colapsar_proyectos:
            # El DISTINCT ON obliga a ordenar primero por su clave, así que el
            # orden del listado se aplica afuera, sobre las filas ya colapsadas.
            sql = text(
                f"{cte_prefix}SELECT * FROM ("
                f" SELECT DISTINCT ON (source, title) {_LIST_COLUMNS}"
                f" {_COLUMNAS_PROYECTO}"
                " FROM properties"
                f" WHERE {where}"
                f" ORDER BY {_REPRESENTANTE}"
                ") proyecto"
                f" ORDER BY {order}"
                " LIMIT :limit OFFSET :offset"
            )
        else:
            sql = text(
                f"{cte_prefix}SELECT {_LIST_COLUMNS}"
                " FROM properties"
                f" WHERE {where}"
                f" ORDER BY {order}"
                " LIMIT :limit OFFSET :offset"
            )
        result = await db.execute(sql, params)
        return [dict(r) for r in result.mappings().all()]

    @staticmethod
    async def list_ids_with_filters(
        db: AsyncSession,
        filters: "PropertyFilters",  # type: ignore[name-defined]
        limit: int = 100,
    ) -> list[int]:
        """Return property IDs matching filters (M6.5 hybrid panel search).

        Same pipeline as list_with_filters (filters + barato CTE + identical
        ORDER BY) but selects only ``id`` with LIMIT and no OFFSET — the SQL
        leg of the RRF fusion, paginated later in memory.
        """
        where, params = _build_filter_sql(filters)
        cte_prefix, where = _apply_barato(filters, where, params)
        order = _orden_listado(filters.state)
        params["limit"] = limit
        sql = text(
            f"{cte_prefix}SELECT id"
            " FROM properties"
            f" WHERE {where}"
            f" ORDER BY {order}"
            " LIMIT :limit"
        )
        result = await db.execute(sql, params)
        return [r["id"] for r in result.mappings().all()]

    @staticmethod
    async def list_by_ids(db: AsyncSession, ids: list[int]) -> list[dict]:
        """Return listing rows for *ids*, preserving the input order.

        ``WHERE id = ANY(:ids)`` does not guarantee order, so rows are
        re-sorted in Python to match the fused ranking (M6.5).
        """
        if not ids:
            return []
        result = await db.execute(
            text(
                f"SELECT {_LIST_COLUMNS}"
                " FROM properties"
                " WHERE id = ANY(:ids)"
            ),
            {"ids": list(ids)},
        )
        by_id = {r["id"]: dict(r) for r in result.mappings().all()}
        return [by_id[i] for i in ids if i in by_id]

    @staticmethod
    async def count_with_filters(
        db: AsyncSession,
        filters: "PropertyFilters",  # type: ignore[name-defined]
        colapsar_proyectos: bool = False,
    ) -> int:
        """Return total count matching filters (no LIMIT/OFFSET).

        `colapsar_proyectos` tiene que ir en el MISMO valor que en
        `list_with_filters`: si una colapsa y la otra no, el total del
        encabezado no coincide con las tarjetas y la paginación miente.
        """
        where, params = _build_filter_sql(filters)
        cte_prefix, where = _apply_barato(filters, where, params)
        cuenta = (
            "COUNT(DISTINCT (source, title))" if colapsar_proyectos else "COUNT(*)"
        )
        sql = text(
            f"{cte_prefix}SELECT {cuenta}"
            " FROM properties"
            f" WHERE {where}"
        )
        result = await db.execute(sql, params)
        return result.scalar() or 0

    @staticmethod
    async def count_by_state(db: AsyncSession) -> dict:
        """Return totals across the active/on_hold/inactive partition in one query.

        Excludes duplicates so the numbers match what the listing actually shows.
        """
        sql = text(
            "SELECT"
            "  SUM(CASE WHEN is_active = TRUE  AND on_hold = FALSE THEN 1 ELSE 0 END) AS active,"
            "  SUM(CASE WHEN is_active = TRUE  AND on_hold = TRUE  THEN 1 ELSE 0 END) AS on_hold,"
            "  SUM(CASE WHEN is_active = FALSE                       THEN 1 ELSE 0 END) AS inactive"
            " FROM properties"
            " WHERE duplicate_of IS NULL"
        )
        row = (await db.execute(sql)).mappings().one()
        return {
            "active": int(row["active"] or 0),
            "on_hold": int(row["on_hold"] or 0),
            "inactive": int(row["inactive"] or 0),
        }

    @staticmethod
    async def count_active_by_city_type(
        db: AsyncSession, pairs: list[tuple[str, str]],
    ) -> dict[tuple[str, str], int]:
        """Stock ACTIVO por combo (city_key, ptype_key) de demanda.

        Activo = is_active AND NOT on_hold AND duplicate_of IS NULL (mismo
        universo que state='active' del listado de propiedades).

        Las claves vienen ya normalizadas con lower(unaccent(trim(...)))
        por los repos de demanda (regla 7). Matching:
        - Ciudad: igualdad exacta por clave normalizada.
        - Tipo: match parcial BIDIRECCIONAL (substring) porque los tipos de
          demanda InfoCasas no comparten slugs con properties — p.ej.
          'duplex' (IC) ↔ 'casa-duplex' (slug), 'locales comerciales' ↔
          'local', 'tinglado o deposito' ↔ 'deposito', 'oficina' ↔
          'oficinas'. Sin match razonable → 0 (honesto, no inventa stock).

        Todos los pares pedidos vienen en el resultado (0 si no hay stock).

        Devuelve ``StockCombo(stock, slug)``. El ``slug`` es el
        ``properties.property_type`` mas frecuente ENTRE LOS QUE SE CONTARON,
        y existe para que el link a `/properties` filtre por lo mismo que se
        conto: ese listado filtra con `property_type = :valor` exacto,
        mientras esta consulta matchea parcial. Mandarle la etiqueta de la
        demanda daria cero en la lista sobre una fila que dice stock 3 —
        otra vez dos numeros distintos en la misma pantalla. Sin stock no hay
        slug (``None``) y el link se queda con la ciudad.
        """
        if not pairs:
            return {}
        sql = (
            text(
                """
                SELECT d.city_key, d.ptype_key, count(p.id) AS stock,
                       mode() WITHIN GROUP (ORDER BY p.property_type) AS slug
                FROM unnest(CAST(:cities AS text[]),
                            CAST(:ptypes AS text[])) AS d(city_key, ptype_key)
                LEFT JOIN properties p
                  ON p.is_active = TRUE
                 AND p.on_hold = FALSE
                 AND p.duplicate_of IS NULL
                 AND lower(unaccent(trim(p.city))) = d.city_key
                 AND NULLIF(lower(unaccent(trim(p.property_type))), '') IS NOT NULL
                 AND (lower(unaccent(trim(p.property_type))) LIKE '%' || d.ptype_key || '%'
                      OR d.ptype_key LIKE '%' || lower(unaccent(trim(p.property_type))) || '%')
                GROUP BY d.city_key, d.ptype_key
                """
            )
            .bindparams(
                bindparam("cities", type_=ARRAY(Text())),
                bindparam("ptypes", type_=ARRAY(Text())),
            )
        )
        result = await db.execute(sql, {
            "cities": [city for city, _ in pairs],
            "ptypes": [ptype for _, ptype in pairs],
        })
        return {
            (r.city_key, r.ptype_key): StockCombo(int(r.stock), r.slug)
            for r in result
        }


property_repo = PropertyRepository()

from __future__ import annotations

import urllib.parse

from app.utils.fotos import url_foto
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.property_repo import property_repo
from app.services.property_service import property_service
from app.utils.money import precio as money_precio
from app.utils.slug import slugify
from app.utils.title import clean_title

PORTAL_SOURCE = "onnixpy"
PORTAL_PAGE_SIZE = 24

# Tipos reales de onnixpy (slugs con guiones, ≠ panel admin).
PORTAL_TIPO_OPTIONS: list[tuple[str, str]] = [
    ("casa", "Casa"),
    ("departamento", "Departamento"),
    ("departamento-en-pozo", "Departamento en pozo"),
    ("casa-en-condominio", "Casa en condominio"),
    ("casa-duplex", "Casa dúplex"),
    ("terreno", "Terreno"),
    ("oficinas", "Oficina"),
    ("local", "Local comercial"),
    ("quinta", "Quinta"),
    ("nave", "Nave / Depósito"),
]
_PORTAL_TIPO_LABELS = dict(PORTAL_TIPO_OPTIONS)


def _slug_publico(row: dict) -> str:
    """El slug de la URL publica, con el titulo ya limpio.

    Las tres URLs de una propiedad —la ficha, la card del listado y la entrada
    del sitemap— tienen que salir de aca y de ningun otro lado. Cuando salian
    de tres expresiones copiadas, cualquier cambio en una dejaba a las otras
    apuntando a una URL que redirige.

    Cuando el titulo no deja nada —107 fichas, todas con el titulo hecho de
    emoji— la URL se arma con el tipo y la operacion en vez de quedarse con la
    ciudad sola: `/prop/2750302-asuncion` es una URL sin SEO, y el dato para
    componerla ya estaba en la fila.
    """
    titulo = clean_title(row.get("title"))
    ciudad = row.get("city") or ""
    if not titulo:
        tipo = (row.get("property_type") or "").replace("-", " ")
        operacion = row.get("operation") or ""
        titulo = " en ".join(p for p in (tipo, operacion) if p)
    return slugify(f"{titulo} {ciudad}".strip())


def format_price_display(price_usd, price_pyg) -> str:
    """Human-readable price: USD first, PYG fallback, else 'Consultar precio'.

    Mismo formato que el panel (``app.utils.money.precio``): el asesor copia el
    precio al portapapeles desde el panel y el cliente abre esta misma página —
    los dos números tienen que escribirse igual. Solo cambia el texto del vacío,
    que acá le habla al cliente.
    """
    return money_precio(price_usd, price_pyg, vacio="Consultar precio")


# Plausible USD price bounds per operation (M6.4b outlier fix). Outside these,
# the PUBLIC views show "Consultar precio" (raw data stays in DB / admin panel).
_PRICE_BOUNDS_USD = {
    "venta": (Decimal("1000"), Decimal("20000000")),      # 1K – 20M
    "alquiler": (Decimal("50"), Decimal("50000")),        # 50 – 50K/mes
}
_PRICE_BOUNDS_DEFAULT = (Decimal("50"), Decimal("20000000"))


def public_price_display(price_usd, price_pyg, operation: str | None) -> str:
    """Like format_price_display, but masks implausible prices (bad source data)."""
    if price_usd:
        lo, hi = _PRICE_BOUNDS_USD.get(operation or "", _PRICE_BOUNDS_DEFAULT)
        if not (lo <= Decimal(price_usd) <= hi):
            return "Consultar precio"
    return format_price_display(price_usd, price_pyg)


def wa_link(phone: str, message: str) -> str:
    """El único armador de deep-links de WhatsApp del portal público.

    Existía dos veces: acá y otra vez adentro de `routes/public.py`, con su
    propio `import urllib.parse` local. Dos armadores del mismo link es el
    patrón que en este repo ya divergió cuatro veces en el panel — y el día que
    haya que cambiar el número o el encoding hay que acordarse de los dos.

    Ningún mensaje se envía desde el código: esto es un `href` que abre
    WhatsApp con el texto escrito, y la persona decide si lo manda.
    """
    return f"https://wa.me/{phone}?text={urllib.parse.quote(message)}"


class PublicPropertyService:
    """Service for the public-facing property detail page (/prop/{id}-{slug}).

    Only properties whose source is in the PUBLIC_SOURCES whitelist, that are
    active, and that are not on hold are eligible for the public view.
    """

    PUBLIC_SOURCES: tuple[str, ...] = ("remax", "onnixpy", "coldwell", "psir")
    WA_NUMBER = "595900000000"

    @staticmethod
    def is_public_eligible(row: dict) -> bool:
        """Return True when a property row may appear in the public view.

        Criteria:
        - source is in PUBLIC_SOURCES whitelist
        - is_active is True (exact equality check)
        - on_hold is falsy (None or False)
        """
        return (
            row.get("source") in PublicPropertyService.PUBLIC_SOURCES
            and row.get("is_active") is True
            and not row.get("on_hold")
        )

    @staticmethod
    async def get_public_detail(db: AsyncSession, prop_id: int) -> dict | None:
        """Return the enriched public detail dict or None if not eligible.

        Delegates to ``property_service.get_property_detail`` (reuses
        photo_urls computation). Does NOT fall back to main_image_url — that
        URL points to the originating portal and must not appear on our
        public page.

        Enriches the returned dict with:
        - ``slug``          URL-safe slug built from title + city
        - ``canonical_path``  e.g. ``/prop/42-casa-en-asuncion``
        - ``public_code``   zero-padded 5-digit string, e.g. ``"00042"``
        - ``wa_url``        WhatsApp deep-link pre-filling a user message
        - ``wa_url_fotos``  idem, pero preguntando por las fotos que faltan
        - ``wa_url_datos``  idem, para la ficha técnica sin ningún dato
        - ``price_display`` human-readable price string
        """
        detail = await property_service.get_property_detail(db, prop_id)
        if detail is None:
            return None
        if not PublicPropertyService.is_public_eligible(detail):
            return None

        # El titulo limpio pisa al crudo en el propio dict: de aca salen el
        # `h1`, el `<title>`, el `og:title` y el alt de la foto, y el visitante
        # no puede ver dos versiones del mismo titulo a un click de distancia.
        detail["title"] = clean_title(detail.get("title"))
        title = detail["title"]
        slug = _slug_publico(detail)
        prop_id_int = detail["id"]
        public_code = f"{prop_id_int:05d}"
        canonical_path = f"/prop/{prop_id_int}-{slug}"

        wa_message = (
            f"Hola! Me interesa la propiedad {public_code} que vi en onnix.com.py"
        )
        wa_url = wa_link(PublicPropertyService.WA_NUMBER, wa_message)
        # El 17,6% del catálogo activo no tiene ni una foto (3.518 de 19.972,
        # medido el 2026-08-23) y esas fichas llegan por el sitemap o por el
        # link que copia el asesor, sin contexto previo. La ficha las resuelve
        # nombrando la ausencia con una acción, y esa acción necesita decir de
        # qué se trata: si el asesor recibe el mismo "me interesa" genérico no
        # sabe que le están pidiendo las fotos.
        wa_url_fotos = wa_link(
            PublicPropertyService.WA_NUMBER,
            f"Hola! ¿Me pasás las fotos de la propiedad {public_code}?",
        )
        # Mismo razonamiento para la ficha técnica vacía (104 fichas, 66 de
        # ellas también sin foto). Y no es solo claridad para quien recibe: con
        # el `wa_url` genérico este enlace y el CTA de «Hablá con un asesor»
        # quedaban a 200px uno del otro apuntando al mismo texto, que es
        # exactamente los dos caminos a la misma acción que prohíbe ui.md.
        wa_url_datos = wa_link(
            PublicPropertyService.WA_NUMBER,
            f"Hola! ¿Me pasás los datos de la propiedad {public_code}?",
        )

        price_display = public_price_display(
            detail.get("price_usd"), detail.get("price_pyg"), detail.get("operation")
        )

        detail["slug"] = slug
        detail["canonical_path"] = canonical_path
        detail["public_code"] = public_code
        detail["wa_url"] = wa_url
        detail["wa_url_fotos"] = wa_url_fotos
        detail["wa_url_datos"] = wa_url_datos
        detail["price_display"] = price_display

        # Strip private/portal-specific fields that must never reach the
        # public page (defense in depth — even if property_service adds new
        # fields in the future, these are always removed explicitly).
        for _private in ("url", "agent_name", "agent_phone", "agent_whatsapp", "main_image_url"):
            detail.pop(_private, None)

        return detail

    @staticmethod
    async def get_portal_listing(
        db: AsyncSession,
        *,
        page: int = 1,
        tipo: str | None = None,
        ciudad: str | None = None,
        operacion: str | None = None,
        precio_min=None,
        precio_max=None,
    ) -> dict:
        """Listado público paginado del portal (M6.4b). Solo onnixpy activas."""
        # Lazy imports: property_service importa este módulo lazy desde
        # _compute_public_path — import a nivel módulo crearía ciclo.
        from app.services.property_service import PropertyFilters
        from app.utils.pagination import calculate_total_pages

        filters = PropertyFilters(
            source=PORTAL_SOURCE,
            state="active",
            property_type=tipo,
            city=ciudad,
            operation=operacion,
            price_min=precio_min,
            price_max=precio_max,
        )
        offset = (page - 1) * PORTAL_PAGE_SIZE
        # El portal colapsa el proyecto en una tarjeta: 407 títulos producían
        # 2.112 de las 5.105 filas. Los dos con el MISMO flag, o el total del
        # encabezado deja de coincidir con las tarjetas.
        rows = await property_repo.list_with_filters(
            db, filters, limit=PORTAL_PAGE_SIZE, offset=offset,
            colapsar_proyectos=True,
        )
        total = await property_repo.count_with_filters(
            db, filters, colapsar_proyectos=True
        )

        cards = []
        for row in rows:
            slug = _slug_publico(row)
            titulo = clean_title(row.get("title"))
            photo_url = None
            if row.get("local_image_count"):
                photo_url = url_foto(row.get("source"), row.get("external_id"))
            ptype = row.get("property_type") or ""
            cards.append({
                "id": row["id"],
                "title": titulo or "Propiedad",
                "public_path": f"/prop/{row['id']}-{slug}",
                "photo_url": photo_url,
                "price_display": public_price_display(
                    row.get("price_usd"), row.get("price_pyg"), row.get("operation")
                ),
                "city": row.get("city"),
                "neighborhood": row.get("neighborhood"),
                "tipo_label": _PORTAL_TIPO_LABELS.get(
                    ptype, ptype.replace("-", " ").replace("_", " ").capitalize()
                ),
                "operation": row.get("operation"),
                "bedrooms": row.get("bedrooms"),
                "bathrooms": row.get("bathrooms"),
                "total_area_m2": row.get("total_area_m2"),
                # >1 cuando la tarjeta representa a un proyecto entero. La
                # plantilla lo usa para decir «85 unidades desde USD X» en vez
                # de repetir la misma foto 85 veces.
                "unidades": row.get("unidades") or 1,
                # Pasa por el mismo enmascarado de precios implausibles que
                # `price_display`: el «desde» de un proyecto no puede escaparse
                # del filtro que la tarjeta suelta sí tiene.
                "precio_desde_display": public_price_display(
                    row.get("precio_desde"), None, row.get("operation")
                ),
            })

        return {
            "cards": cards,
            "total": total,
            "total_pages": calculate_total_pages(total, PORTAL_PAGE_SIZE),
            "page": page,
        }

    @staticmethod
    async def get_sitemap_entries(db: AsyncSession) -> list[dict]:
        """Return loc/lastmod pairs for public XML sitemap generation.

        Queries only the whitelisted sources with the same eligibility
        criteria used by ``is_public_eligible``.  Returns entries in id
        order for stable output.
        """
        rows = await property_repo.get_public_sitemap_rows(
            db, PublicPropertyService.PUBLIC_SOURCES
        )
        entries = []
        for row in rows:
            slug = _slug_publico(row)
            prop_id = row["id"]
            loc = f"/prop/{prop_id}-{slug}"

            updated_at = row.get("updated_at")
            if updated_at is not None:
                try:
                    lastmod = updated_at.date().isoformat()
                except AttributeError:
                    lastmod = None
            else:
                lastmod = None

            entries.append({"loc": loc, "lastmod": lastmod})
        return entries


public_property_service = PublicPropertyService()

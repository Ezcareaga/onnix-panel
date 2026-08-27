"""El parseo del catálogo de InfoCasas contra la API nueva de oficina virtual.

InfoCasas decomisionó `sitio/?mid=inmobiliarias&func=ajax_panel_findPropiedades`
y movió el panel de inmobiliarias a `graph.infocasas.com.uy`. No es un cambio de
URL: 9 de los 23 campos que leía `parse_property()` dejaron de existir con ese
nombre y `activo` pasó de string `'1'` a entero `1`.

**Las dos filas de abajo son reales**, copiadas de la respuesta del
2026-08-24 (`POST /api/1.0/listing/virtual-office?page=1&limit=500&status[]=1`,
9.411 activas). Se les recortó `descripcion` y `facilities` porque no se leen;
todo lo demás es verbatim, tipos incluidos. Ningún nombre de campo de este
archivo fue inventado.

El test que estos reemplazan —`test_scraper_ic_login_button.py`— asserteaba que
nuestro propio fuente contuviera la etiqueta de un botón, contra un `page`
mockeado: no podía ver un cambio del portal ni aunque el modal desapareciera,
que es exactamente lo que pasó.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_home_dir = str(Path(__file__).resolve().parent.parent.parent)
_scrapers_dir = str(Path(__file__).resolve().parent.parent.parent / "scrapers")
for _p in (_home_dir, _scrapers_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scrapers.infocasas import scraper as ic  # noqa: E402


# ---------------------------------------------------------------------------
# Dos filas reales de la respuesta del 2026-08-24
# ---------------------------------------------------------------------------

VENTA_PYG = {
    "IDdepartamentos": 22,
    "IDtipos": 1,
    "IDzonas": 427,
    "activo": 1,
    "bathrooms": 2,
    "bedrooms": 3,
    "codigo": "XFADD8",
    "consultas": 0,
    "currency_id": 3,
    "direccion": "Cañada Garay, Luque, Luque, Central, Paraguay",
    "fechaReg": "2026-08-21 00:00:00",
    "id": 194143354,
    "img_total": 6,
    "lat": "-25.25090646432787000000",
    "location": "Luque Central",
    "location_main": None,
    "long": "-57.46079138648496000000",
    "m2": 82,
    "m2edificados": 82,
    "m2terreno": 136,
    "operation_type": {
        "country_id": 2,
        "id": 6,
        "name": "Venta",
        "operation_type_id": 1,
        "plural": "Ventas",
    },
    "operation_type_id": 1,
    "precioa": 0,
    "price": 440000000,
    "price_usd": 73234.47,
    "property_type": {
        "country_id": 2,
        "id": 13,
        "name": "Casa",
        "plural": "Casas",
        "property_type_id": 1,
    },
    "summary_leads": {"Consultas": 0, "Telefono": 0, "Whatsapp": 4},
    "titulo": "Casas en venta en Barrio Cerrado Arandú | Cañada Garay, Luque",
    "type": "propiedad",
    "visitas": 2,
}

ALQUILER_USD = {
    "IDdepartamentos": 21,
    "IDtipos": 2,
    "IDzonas": 2827,
    "activo": 1,
    "bathrooms": 1,
    "bedrooms": 2,
    "codigo": "DE8434",
    "consultas": 0,
    "currency_id": 1,
    "direccion": "Av santa teresa sky tower",
    "fechaReg": "2026-08-21 00:00:00",
    "id": 194144664,
    "img_total": 9,
    "lat": "-25.28849810000000000000",
    "location": "Asunción Asunción",
    "location_main": None,
    "long": "-57.55518730000000000000",
    "m2": 102,
    "m2edificados": 102,
    "m2terreno": None,
    "operation_type": {
        "country_id": 2,
        "id": 7,
        "name": "Alquiler",
        "operation_type_id": 2,
        "plural": "Alquileres",
    },
    "operation_type_id": 2,
    "precioa": 1500,
    "price": 1500,
    "price_usd": 1500,
    "property_type": {
        "country_id": 2,
        "id": 14,
        "name": "Departamento",
        "plural": "Departamentos",
        "property_type_id": 2,
    },
    "summary_leads": {"Consultas": 0, "Telefono": 0, "Whatsapp": 0},
    "titulo": "Alquier departamento ",
    "type": "propiedad",
    "visitas": 3,
}


# ---------------------------------------------------------------------------
# `activo`: el que hace daño en silencio
# ---------------------------------------------------------------------------

class TestActivo:
    """`activo` es entero en la API nueva. Comparado contra el string `'1'`
    del parser viejo, TODA propiedad entraba con is_active=False."""

    def test_activo_entero_uno_es_activa(self):
        assert ic.parse_property(VENTA_PYG)["is_active"] is True

    def test_activo_entero_cero_es_inactiva(self):
        assert ic.parse_property({**VENTA_PYG, "activo": 0})["is_active"] is False

    def test_activo_string_uno_sigue_siendo_activa(self):
        """Si IC vuelve al string, el parser no se rompe en el otro sentido."""
        assert ic.parse_property({**VENTA_PYG, "activo": "1"})["is_active"] is True

    def test_activo_ausente_es_inactiva(self):
        raw = {k: v for k, v in VENTA_PYG.items() if k != "activo"}
        assert ic.parse_property(raw)["is_active"] is False


# ---------------------------------------------------------------------------
# Precio y moneda
# ---------------------------------------------------------------------------

class TestPrecios:
    """`precioRealV`/`precioRealA` → `price`/`precioa`;
    `IDmonedasv`/`IDmonedasa` → `currency_id` (1=USD, 3=PYG)."""

    def test_venta_toma_price_en_la_moneda_de_currency_id(self):
        p = ic.parse_property(VENTA_PYG)
        assert p["price_sale"] == Decimal("440000000")
        assert p["currency_sale"] == "PYG"

    def test_venta_sin_precio_de_alquiler_deja_price_rent_en_none(self):
        p = ic.parse_property(VENTA_PYG)
        assert p["price_rent"] is None
        assert p["currency_rent"] is None

    def test_alquiler_toma_precioa_y_no_deja_precio_de_venta(self):
        p = ic.parse_property(ALQUILER_USD)
        assert p["price_rent"] == Decimal("1500")
        assert p["currency_rent"] == "USD"
        assert p["price_sale"] is None
        assert p["currency_sale"] is None

    def test_currency_id_uno_es_dolar(self):
        p = ic.parse_property({**VENTA_PYG, "currency_id": 1, "price": 190000})
        assert p["currency_sale"] == "USD"

    def test_currency_id_desconocido_no_inventa_moneda(self):
        p = ic.parse_property({**VENTA_PYG, "currency_id": 99})
        assert p["currency_sale"] is None

    def test_precio_que_desbordaria_numeric_15_2_se_descarta(self):
        """`price` vino con 9223372036854775807 en el catálogo real: es basura
        y además revienta NUMERIC(15,2), que corta en 1e13."""
        p = ic.parse_property({**VENTA_PYG, "price": 9223372036854775807})
        assert p["price_sale"] is None

    def test_precio_cero_no_es_precio(self):
        assert ic.parse_property({**VENTA_PYG, "price": 0})["price_sale"] is None


# ---------------------------------------------------------------------------
# Tipo y operación
# ---------------------------------------------------------------------------

class TestTipoYOperacion:
    """`tipoPropiedad` → `property_type.plural`;
    `operacion` → `operation_type.name`."""

    def test_tipo_sale_del_plural_del_objeto_property_type(self):
        assert ic.parse_property(VENTA_PYG)["property_type"] == "casa"
        assert ic.parse_property(ALQUILER_USD)["property_type"] == "departamento"

    def test_tipo_fuera_del_mapa_cae_al_label_en_minuscula(self):
        raw = {**VENTA_PYG, "property_type": {"plural": "Locales Comerciales"}}
        assert ic.parse_property(raw)["property_type"] == "locales comerciales"

    def test_tipo_ausente_no_revienta(self):
        raw = {k: v for k, v in VENTA_PYG.items() if k != "property_type"}
        assert ic.parse_property(raw)["property_type"] == ""

    def test_operacion_sale_del_name_del_objeto_operation_type(self):
        assert ic.parse_property(VENTA_PYG)["operation"] == "venta"
        assert ic.parse_property(ALQUILER_USD)["operation"] == "alquiler"

    def test_operacion_ausente_no_revienta(self):
        raw = {k: v for k, v in VENTA_PYG.items() if k != "operation_type"}
        assert ic.parse_property(raw)["operation"] == ""


# ---------------------------------------------------------------------------
# Geografía
# ---------------------------------------------------------------------------

class TestGeografia:
    """`IDdepartamentos` pasó de string a entero, y el mapa viejo estaba mal:
    salvo 21 y 22, ninguno de los 18 IDs nombraba su departamento.

    Medido el 2026-08-24 sobre las 9.411 activas: para cada `IDdepartamentos`
    el sufijo de `location` es constante, y es el nombre del departamento.
    ID 25 aparece 367 veces, siempre con `location` terminada en 'Cordillera'
    ('San Bernardino Cordillera'); el mapa viejo lo llamaba 'Caaguazú'.
    """

    def test_departamento_veintidos_es_central(self):
        assert ic.parse_property(VENTA_PYG)["department"] == "Central"

    def test_departamento_veinticinco_es_cordillera_no_caaguazu(self):
        raw = {**VENTA_PYG, "IDdepartamentos": 25, "location": "San Bernardino Cordillera"}
        assert ic.parse_property(raw)["department"] == "Cordillera"

    def test_departamento_treinta_y_ocho_es_caaguazu(self):
        raw = {**VENTA_PYG, "IDdepartamentos": 38, "location": "Coronel Oviedo Caaguazú"}
        assert ic.parse_property(raw)["department"] == "Caaguazú"

    def test_departamento_desconocido_queda_vacio(self):
        assert ic.parse_property({**VENTA_PYG, "IDdepartamentos": 999})["department"] == ""

    def test_ciudad_sale_de_direccion_no_de_location(self):
        """En Asunción `location` trae el barrio ('Sajonia Asunción'), no la
        ciudad. Por eso `direccion` sigue siendo la fuente principal: cambiarla
        movería el matching por geo+precio, que compara contra properties.city.
        """
        raw = {**VENTA_PYG, "direccion": "Sajonia, Asunción, Paraguay",
               "location": "Sajonia Asunción", "IDdepartamentos": 21}
        assert ic.parse_property(raw)["city"] == "Asunción"

    def test_ciudad_sin_direccion_cae_a_location_sin_el_departamento(self):
        """`direccion` no da nada en 2.379 de las 9.411 — falta, o viene de una
        sola parte. Con el fallback, las que coinciden con properties.city pasan
        de 6.911 a 8.582 (medido el 2026-08-24)."""
        assert ic.parse_property({**VENTA_PYG, "direccion": None})["city"] == "Luque"

    def test_ciudad_cuando_ciudad_y_departamento_se_llaman_igual(self):
        """`direccion` de una sola parte → fallback; 'Asunción Asunción' no
        puede quedar vacía por sacarle el sufijo."""
        assert ic.parse_property(ALQUILER_USD)["city"] == "Asunción"

    def test_barrio_sale_de_direccion_cuando_tiene_tres_partes_o_mas(self):
        assert ic.parse_property(VENTA_PYG)["neighborhood"] == "Cañada Garay"

    def test_barrio_vacio_cuando_la_direccion_es_una_sola_parte(self):
        assert ic.parse_property(ALQUILER_USD)["neighborhood"] == ""

    def test_lat_y_lng_salen_de_lat_y_long(self):
        p = ic.parse_property(VENTA_PYG)
        assert p["lat"] == Decimal("-25.25090646432787000000")
        assert p["lng"] == Decimal("-57.46079138648496000000")

    def test_lat_en_cero_no_es_una_coordenada(self):
        p = ic.parse_property({**VENTA_PYG, "lat": "0", "long": "0"})
        assert p["lat"] is None and p["lng"] is None


# ---------------------------------------------------------------------------
# Identidad, superficie y contadores
# ---------------------------------------------------------------------------

class TestRestoDeCampos:

    def test_id_y_codigo_son_las_dos_claves(self):
        p = ic.parse_property(VENTA_PYG)
        assert p["infocasas_id"] == "194143354"
        assert p["infocasas_ref"] == "XFADD8"

    def test_url_se_arma_con_el_id_porque_link_ya_no_viene(self):
        """`link` no existe en la API nueva. InfoCasas hace 301 de cualquier
        slug al canónico mientras el id sea correcto (verificado 2026-08-24:
        /x/193386588 → 301 a /duplex-alquiler-luque-paraguay/193386588)."""
        url = ic.parse_property(VENTA_PYG)["url"]
        assert url.startswith("https://www.infocasas.com.py/")
        assert url.endswith("/194143354")

    def test_url_no_deja_el_slug_vacio(self):
        url = ic.parse_property({**VENTA_PYG, "titulo": "   "})["url"]
        assert "//194143354" not in url.replace("https://", "")

    def test_superficies_salen_de_m2_y_m2edificados(self):
        p = ic.parse_property(VENTA_PYG)
        assert p["total_area_m2"] == Decimal("82")
        assert p["built_area_m2"] == Decimal("82")

    def test_superficie_absurda_se_descarta(self):
        """`m2edificados` llegó a 2147480000 en el catálogo real."""
        p = ic.parse_property({**VENTA_PYG, "m2edificados": 2147480000})
        assert p["built_area_m2"] is None

    def test_dormitorios_y_banos(self):
        p = ic.parse_property(VENTA_PYG)
        assert p["bedrooms"] == 3
        assert p["bathrooms"] == 2

    def test_whatsapp_sale_de_summary_leads(self):
        """El campo `whatsapp` de primer nivel desapareció; el contador vive
        en `summary_leads.Whatsapp`."""
        assert ic.parse_property(VENTA_PYG)["whatsapp"] == 4
        assert ic.parse_property(ALQUILER_USD)["whatsapp"] == 0

    def test_consultas_y_visitas_siguen_en_primer_nivel(self):
        p = ic.parse_property({**VENTA_PYG, "consultas": 7, "visitas": 9})
        assert p["consultas"] == 7
        assert p["visitas"] == 9

    def test_vendedor_ya_no_viene_y_no_se_inventa(self):
        assert ic.parse_property(VENTA_PYG)["vendedor"] == ""

    def test_titulo_y_direccion(self):
        p = ic.parse_property(VENTA_PYG)
        assert p["title"] == "Casas en venta en Barrio Cerrado Arandú | Cañada Garay, Luque"
        assert p["address"] == "Cañada Garay, Luque, Luque, Central, Paraguay"


# ---------------------------------------------------------------------------
# Ningún campo muerto
# ---------------------------------------------------------------------------

class _DictQueEspia(dict):
    """dict que anota qué claves le pidieron."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.pedidas: set[str] = set()

    def get(self, key, default=None):  # noqa: D102
        self.pedidas.add(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.pedidas.add(key)
        return super().__getitem__(key)


def test_parse_property_no_lee_ningun_campo_que_la_api_nueva_no_manda():
    """Los 9 nombres muertos (`precioRealV`, `IDmonedasv`, `tipoPropiedad`,
    `operacion`, `link`, `whatsapp`, `vendedor`, …) no se leen más.

    Se mide leyendo: un dict que anota qué claves le pidieron. Cualquier lectura
    de un campo que no está en la respuesta real deja rastro acá.
    """
    espia = _DictQueEspia(VENTA_PYG)
    ic.parse_property(espia)
    muertas = espia.pedidas - set(VENTA_PYG)
    assert not muertas, f"parse_property lee campos que la API ya no manda: {sorted(muertas)}"


# ---------------------------------------------------------------------------
# El fetch: sin Playwright, con el token del poll de leads
# ---------------------------------------------------------------------------

class TestFetch:

    def test_get_token_lee_el_mismo_setting_que_mantiene_el_session_manager(self):
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchone.return_value = ("jwt-de-prueba",)
        conn = MagicMock()
        conn.cursor.return_value = cur

        assert ic.get_token(conn) == "jwt-de-prueba"
        sql, params = cur.execute.call_args.args
        assert "bot_settings" in sql
        assert params == ("infocasas_frontend_token",)

    def test_get_token_sin_token_aborta(self):
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchone.return_value = None
        conn = MagicMock()
        conn.cursor.return_value = cur

        with pytest.raises(SystemExit):
            ic.get_token(conn)

    def test_la_sesion_manda_bearer_y_x_origin(self):
        """Sin `x-origin` la API contesta 400 Missing origin header."""
        s = ic.build_session("jwt-de-prueba")
        assert s.headers["Authorization"] == "Bearer jwt-de-prueba"
        assert s.headers["x-origin"] == "www.infocasas.com.py"

    def test_fetch_page_pega_al_endpoint_nuevo_y_filtra_por_activas(self):
        session = MagicMock()
        session.post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"response": {"data": [], "total": 9411,
                                                      "last_page": 19}, "code": 200}),
        )

        out = ic.fetch_page(session, 3)

        url = session.post.call_args.args[0]
        params = session.post.call_args.kwargs["params"]
        assert url == "https://graph.infocasas.com.uy/api/1.0/listing/virtual-office"
        assert params["page"] == 3
        assert params["status[]"] == 1
        assert out["total"] == 9411

    def test_fetch_page_devuelve_none_cuando_la_api_no_da_doscientos(self):
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=401, text="unauthenticated")
        with patch.object(ic.time, "sleep"):
            assert ic.fetch_page(session, 1, max_retries=2) is None

    def test_el_scraper_ya_no_importa_playwright(self):
        """El camino nuevo va con `requests` pelado. Playwright y Chromium
        siguen instalados en el VPS para abrir la oficina virtual a mano, pero
        este scraper no los toca."""
        fuente = Path(ic.__file__).read_text()
        codigo = "\n".join(
            l for l in fuente.splitlines() if not l.lstrip().startswith("#")
        )
        assert "playwright" not in codigo.lower()

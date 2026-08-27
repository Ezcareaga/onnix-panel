"""Los titulos publicos, sin lo que la fuente no puede dibujar.

Los 19.972 titulos del portal son copy escrito a mano en otro sistema. Medido
el 2026-08-23: 62 usan glifos matematicos —fuera del `unicode-range` de los dos
`@font-face` de Outfit, o sea que el `h1` se dibuja con la fuente del sistema y
un lector de pantalla los deletrea—, 294 traen emoji, y 107 dejan el slug vacio
porque el titulo se evapora al slugificar (`/prop/2750302-asuncion`, verificado
por `curl`).

Ez decidio limpieza de caracteres, no reescritura de titulos. La diferencia es
lo que este archivo protege: **las mayusculas, el orden de las palabras y la
puntuacion quedan intactos**, y `mburucuya` y `villa morra` no se tocan. La
alternativa descartada era normalizar 19.923 filas de copy con logica de
verdad.

Los casos de abajo no son inventados: son las formas que aparecen en la tabla.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.public_property_service import _slug_publico
from app.utils.title import clean_title

_SERVICE = (
    Path(__file__).resolve().parent.parent
    / "app" / "services" / "public_property_service.py"
)


# ---------------------------------------------------------------------------
# clean_title — que se va y que NO se va
# ---------------------------------------------------------------------------

class TestLoQueSeVa:
    @pytest.mark.parametrize("crudo,limpio", [
        # Los glifos matematicos se CONVIERTEN, no se borran: el titulo sigue
        # diciendo lo mismo y ademas se puede dibujar.
        ("\U0001D407\U0001D404\U0001D411\U0001D40C\U0001D40E\U0001D412\U0001D400 casa",
         "HERMOSA casa"),
        ("\U0001D5E9\U0001D5F2\U0001D5FB\U0001D601\U0001D5EE en Luque",
         "Venta en Luque"),
        # Emoji: `ui.md` los prohibe por nombre.
        ("\U0001F3E1 Casa en Lambare \U0001F31F", "Casa en Lambare"),
        # El selector de variacion viaja pegado al emoji y sin sacarlo queda un
        # hueco invisible que despues aparece como espacio doble.
        ("VENTA ✅ Duplex ➡️ Fernando de la Mora",
         "VENTA Duplex Fernando de la Mora"),
        ("  Casa  con   espacios   ", "Casa con espacios"),
    ])
    def test_el_titulo_queda_dibujable(self, crudo, limpio):
        assert clean_title(crudo) == limpio

    def test_un_titulo_de_puro_emoji_no_deja_nada(self):
        """Es el caso de los 107 slugs vacios. Devuelve cadena vacia y el
        llamador decide el reemplazo: el de la card no es el del slug."""
        assert clean_title("\U0001F3E0\U0001F3E0\U0001F3E0") == ""

    @pytest.mark.parametrize("vacio", [None, "", "   "])
    def test_lo_vacio_no_explota(self, vacio):
        assert clean_title(vacio) == ""


class TestLoQueNoSeVa:
    """La limpieza saca caracteres. Todo lo demas es exactamente el original."""

    @pytest.mark.parametrize("titulo", [
        "Terreno en Mburucuya, Itapua",
        "Ñemby, casa a estrenar",
        "villa morra - departamento",
        "CASA EN VENTA ZONA VILLA MORRA",
        "Casa 3 dorm. — Luque",
        "Depto a ½ cuadra de Avda. Espana",
        # NFKC sobre la cadena entera dejaba `100 m2`, `No 5` y `1⁄2`: son
        # caracteres que la fuente SI dibuja, y reescribirlos es lo que la
        # decision descarto.
        "Local comercial 100 m² en Asuncion",
        "Nº 5, Villa Morra",
        "Duplex en Fernando de la Mora (zona norte)",
        "Local comercial 100 m2, USD 1.200",
    ])
    def test_el_titulo_pasa_intacto(self, titulo):
        assert clean_title(titulo) == titulo

    def test_las_mayusculas_no_se_tocan(self):
        """9.042 de 19.972 titulos (45,3%) estan enteros en mayusculas. Bajarlos
        es reescribir copy y NO es lo que se decidio: destruye `Mburucuya` y
        cualquier sigla."""
        gritado = "HERMOSA CASA EN VENTA - VILLA MORRA - ASUNCION"
        assert clean_title(gritado) == gritado


# ---------------------------------------------------------------------------
# El slug publico
# ---------------------------------------------------------------------------

class TestSlugPublico:
    def test_el_titulo_limpio_es_el_que_llega_a_la_url(self):
        fila = {"title": "\U0001F3E1 Casa en Villa Morra \U0001F31F",
                "city": "Asuncion"}
        assert _slug_publico(fila) == "casa-en-villa-morra-asuncion"

    def test_sin_titulo_la_url_dice_que_es_y_no_solo_donde(self):
        """`/prop/2750302-asuncion` es una URL sin SEO. El tipo y la operacion
        ya estaban en la fila."""
        fila = {"title": "\U0001F3E0\U0001F3E0\U0001F3E0", "city": "Asuncion",
                "property_type": "casa-duplex", "operation": "venta"}
        assert _slug_publico(fila) == "casa-duplex-en-venta-asuncion"

    def test_sin_titulo_y_sin_tipo_queda_el_fallback_de_slugify(self):
        assert _slug_publico({"title": None, "city": None}) == "propiedad"

    def test_los_acentos_del_guarani_sobreviven_al_slug(self):
        fila = {"title": "Casa en Mburucuya", "city": "Itapua"}
        assert _slug_publico(fila) == "casa-en-mburucuya-itapua"


class TestUnSoloLugarQueArmaLaURL:
    """La ficha, la card del listado y el sitemap tienen que componer el MISMO
    slug. Cuando salia de tres expresiones copiadas, cambiar una dejaba a las
    otras apuntando a una URL que redirige — y el sitemap listando esa.

    Es el patron que ya mordio cuatro veces en este repo: lo escrito dos veces
    diverge.
    """

    def test_el_service_slugifica_en_un_solo_lugar(self):
        fuente = _SERVICE.read_text(encoding="utf-8")
        # Sin comentarios ni docstrings: el comentario que explica la regla
        # nombra lo que la regla prohibe.
        sin_docstrings = re.sub(r'"""(?:.|\n)*?"""', "", fuente)
        sin_comentarios = re.sub(r"(?m)#.*$", "", sin_docstrings)
        llamadas = re.findall(r"\bslugify\(", sin_comentarios)
        assert len(llamadas) == 1, (
            f"{len(llamadas)} llamadas a slugify() en el service: la URL "
            "publica se compone en `_slug_publico` y en ningun otro lado"
        )

    def test_las_tres_filas_dan_el_mismo_slug(self):
        """La fila de la ficha, la del listado y la del sitemap traen los mismos
        cuatro campos. Si el sitemap perdiera `property_type`, las URLs sin
        titulo divergirian sin que nadie lo note."""
        campos = {"title": "\U0001F3E0", "city": "Luque",
                  "property_type": "terreno", "operation": "alquiler"}
        ficha = _slug_publico(dict(campos, id=1))
        card = _slug_publico(dict(campos, local_image_count=3))
        sitemap = _slug_publico(dict(campos, updated_at=None))
        assert ficha == card == sitemap == "terreno-en-alquiler-luque"

    def test_el_sitemap_pide_las_columnas_que_el_slug_necesita(self):
        """Guarda contra el rojo silencioso: si alguien saca las columnas de la
        consulta, `_slug_publico` recibe None y el sitemap publica URLs que
        redirigen — sin fallar nada."""
        repo = (
            Path(__file__).resolve().parent.parent
            / "app" / "repositories" / "property_repo.py"
        ).read_text(encoding="utf-8")
        select = re.search(r'"SELECT id, title, city,([^"]*)"', repo)
        assert select, "cambio la forma del SELECT del sitemap"
        for columna in ("property_type", "operation"):
            assert columna in select.group(1), (
                f"el sitemap dejo de traer `{columna}`: sus URLs sin titulo "
                "van a divergir de las de la ficha"
            )

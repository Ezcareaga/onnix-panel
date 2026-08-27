"""La ficha pública sin foto — F2, F3 y F10 de `docs/audit/FICHA_PLAN_20260823.md`.

Qué cierra, con el número que lo dimensiona (medido en `onnix_prod`
el 2026-08-23): **3.518 de 19.972 fichas activas no tienen ni una foto**
(17,61 %), y **3.467 de esas son de `remax`** — o sea que casi ninguna se
alcanza desde `/propiedades`, que lista solo `onnixpy`. Llegan por el
sitemap y por el link que copia el asesor: entradas frías.

Para esas fichas el hero era una caja de 548 px que sostenía un gradiente y un
monograma de 64 px, y a 1280 px el monograma caía **entero** adentro de la caja
del `h1` en el 48,8 % de los casos.

**Este archivo es el piso, no el verde.** El criterio de verde de una tarea
visual es el navegador a 390 / 768 / 1280 (`CLAUDE.md`, «un test verde no es un
test que prueba algo»). Acá se assertea lo que sí es lógica: qué se emite y qué
no, y los dos números —contraste y touch target— que se **calculan**, nunca se
copian de un comentario.

El CSS se lee sin comentarios por la trampa propia de este repo: el comentario
que explica por qué se fue la grilla nombra la grilla.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from tests.plantillas_publicas import fuente_de, sin_comentarios

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
FICHA = TEMPLATES / "public" / "property.html"

# Dos fotos: alcanza para que el mosaico tenga su forma por defecto.
CON_FOTO = ["/images/remax/E1/1.webp", "/images/remax/E1/2.webp"]

_COMENTARIO_CSS = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMENTARIO_JINJA = re.compile(r"\{#.*?#\}", re.DOTALL)
_COMENTARIO_HTML = re.compile(r"<!--.*?-->", re.DOTALL)


# Reexportado: `test_ficha_reglas_ui` lo importa desde acá desde antes de que
# el lector compartido existiera.
_sin_comentarios = sin_comentarios


@pytest.fixture(scope="module")
def fuente() -> str:
    """La plantilla, con sus includes resueltos y sin comentarios."""
    return fuente_de(FICHA)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def _prop(**over) -> dict:
    """Un detail como el que devuelve `PublicPropertyService.get_public_detail`."""
    base = {
        "id": 2755478,
        "title": "Condominio University Park",
        "city": "Luque",
        "neighborhood": "Zona Aeropuerto",
        "description": "Casa amplia",
        "bedrooms": 3,
        "bathrooms": 2,
        "parking": 1,
        "total_area_m2": 150.0,
        "construction_state": "usado",
        "operation": "venta",
        "property_type": "casa",
        "latitude": None,
        "longitude": None,
        "photo_urls": [],
        "price_display": "USD 120.000",
        "wa_url": "https://wa.me/595900000000?text=Hola%20me%20interesa",
        "wa_url_fotos": "https://wa.me/595900000000?text=Hola%20las%20fotos",
        "wa_url_datos": "https://wa.me/595900000000?text=Hola%20los%20datos",
        "slug": "condominio-university-park-luque",
        "public_code": "02755478"[:5],
        "canonical_path": "/prop/2755478-condominio-university-park-luque",
    }
    base.update(over)
    return base


def _render(prop: dict, asesor_wa_url=None, asesor_wa_url_fotos=None,
            asesor_wa_url_datos=None) -> str:
    """Renderiza la ficha igual que la sirve Starlette.

    `autoescape=True` porque eso es lo que hace `Jinja2Templates`; sin eso el
    macro `foto()` no se comporta como en producción. `clean_description` se
    registra como identidad: este archivo no prueba ese filtro, y traerlo
    obligaría a importar `app.tz` entero.
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    env.filters["clean_description"] = lambda s: s
    tpl = env.get_template("public/property.html")
    html = tpl.render(
        prop=prop,
        base_url="https://onnix.com.py",
        asesor_wa_url=asesor_wa_url,
        asesor_wa_url_fotos=asesor_wa_url_fotos,
        asesor_wa_url_datos=asesor_wa_url_datos,
        request=None,
    )
    return html


def _body(prop: dict, **kw) -> str:
    """Solo el `<body>`: el markup emitido, sin el `<style>` inline.

    No es una comodidad. La primera versión de estos tests assertaba sobre el
    documento entero y daba rojo porque `.hero-overlay` aparece en la hoja de
    estilos aunque el elemento no se emita — y por el mismo motivo un
    `assert "hero-flat" in html` habría dado **verde con el markup ausente**.
    La regla que se rompe es la 5 del `CLAUDE.md`: assert por substring contra
    un texto que contiene lo que se busca por otra razón.
    """
    html = _render(prop, **kw)
    return html.split("<body>", 1)[1]


# ---------------------------------------------------------------------------
# F2 — sin foto no hay hero
# ---------------------------------------------------------------------------

class TestElDatoNuncaVaEncimaDeLaFoto:
    """La composición del 23/08: el precio y el título salen de la foto.

    Antes había DOS cabeceras —`hero` con el texto superpuesto sobre la imagen y
    `hero-flat` para la ficha sin foto— y el monograma de 64 px del placeholder
    solapaba el `h1`: 82,9 px a 390 y los 102,4 enteros a 1280.

    El arreglo no fue separar más el monograma: fue que el texto deje de vivir
    adentro del bloque de fotos. **13 de 13 referencias medidas ponen el precio
    fuera de la imagen y ninguna lo superpone**
    (`FICHA_REFERENCIAS_20260823.md` §Conclusión 1). Con el texto afuera, el
    solape no puede existir en ninguna de las dos ramas, y de paso se va el
    `.hero-overlay` —que era lo que hacía que el contraste del precio dependiera
    de la foto que bajó el scraper.
    """

    @pytest.mark.parametrize("fotos", [[], CON_FOTO])
    def test_el_precio_y_el_titulo_estan_fuera_del_bloque_de_fotos(self, fotos):
        html = _body(_prop(photo_urls=fotos))
        galeria = re.search(
            r'<section class="galeria.*?</section>', html, re.DOTALL
        )
        assert galeria is not None, "no se emitió el bloque de fotos"
        adentro = galeria.group(0)
        assert "datos-precio" not in adentro
        assert "<h1" not in adentro

    def test_el_overlay_y_el_backdrop_no_existen_mas(self, fuente):
        """No queda el mecanismo, no solo su uso: mientras el CSS esté, alguien
        lo vuelve a poner."""
        assert "hero-overlay" not in fuente
        assert "hero-backdrop" not in fuente

    def test_una_sola_cabecera_para_las_dos_ramas(self):
        """No es que las dos digan lo mismo: es que hay una sola.

        Dos superficies excluyentes por breakpoint —o por rama— es el patrón
        dominante en las referencias Y la trampa que en este panel ya produjo
        cuatro duplicados divergidos.
        """
        for fotos in ([], CON_FOTO):
            html = _body(_prop(photo_urls=fotos))
            assert len(re.findall(r'class="datos"', html)) == 1
            assert len(re.findall(r"<h1\b", html)) == 1

    def test_sin_fotos_el_dato_sigue_estando(self):
        html = _body(_prop(photo_urls=[], title="Condominio University Park"))
        assert "galeria-sin-fotos" in html
        assert "Condominio University Park" in html
        assert "USD 120.000" in html
        assert "Zona Aeropuerto" in html

    def test_sin_fotos_la_ausencia_es_accion(self):
        html = _body(_prop(photo_urls=[]))
        assert "Preguntá por las fotos" in html

    def test_con_fotos_el_placeholder_del_onerror_sigue_vivo(self):
        """`photo_urls` sale de `local_image_count`, no de mirar el disco, y los
        dos ya divergieron. Con foto declarada el placeholder se emite oculto y
        `onerror` lo destapa: esa rama sigue necesitando su CSS."""
        html = _body(_prop(photo_urls=["/images/remax/E1/1.webp"]))
        assert "galeria-placeholder" in html
        assert "monogram" in html

    def test_el_css_del_placeholder_sigue_declarado(self, fuente):
        """Corolario del anterior, del lado del CSS: borrar la regla dejaría el
        camino degradado sin estilo."""
        assert ".galeria-placeholder {" in fuente
        assert ".galeria-vacia {" in fuente


# ---------------------------------------------------------------------------
# F2 / F10 — la ausencia es una acción, no un blanco
# ---------------------------------------------------------------------------

class TestAusenciaComoAccion:
    def test_sin_fotos_hay_un_enlace_a_whatsapp_por_las_fotos(self):
        html = _body(_prop(photo_urls=[]))
        assert "wa.me/595900000000?text=Hola%20las%20fotos" in html
        assert "falta-link" in html

    def test_el_enlace_de_fotos_respeta_la_atribucion_del_asesor(self):
        """Si el visitante llegó por el link de un asesor (`?a=`), la pregunta
        va al asesor, no a la oficina — igual que los otros dos CTA."""
        html = _body(
            _prop(photo_urls=[]),
            asesor_wa_url_fotos="https://wa.me/595991000111?text=fotos-asesor",
        )
        assert "wa.me/595991000111?text=fotos-asesor" in html

    def test_con_fotos_no_se_pregunta_por_las_fotos(self):
        html = _body(_prop(photo_urls=["/images/remax/E1/1.webp"]))
        assert "wa.me/595900000000?text=Hola%20las%20fotos" not in html

    def test_sin_ningun_dato_no_se_emite_la_seccion_caracteristicas(self):
        """104 fichas quedaban con la etiqueta «Características» sobre un div
        vacío, y 66 de ellas eran fichas sin foto."""
        html = _body(_prop(bedrooms=None, bathrooms=None, parking=None,
                             total_area_m2=None, construction_state=None))
        assert 'id="ficha-label"' not in html
        assert ">Características<" not in html

    def test_sin_ningun_dato_va_la_accion_en_su_lugar(self):
        html = _body(_prop(bedrooms=None, bathrooms=None, parking=None,
                             total_area_m2=None, construction_state=None))
        assert "falta-datos" in html
        assert "wa.me/595900000000?text=Hola%20los%20datos" in html

    def test_la_accion_de_los_datos_no_repite_el_cta_de_la_pagina(self):
        """El CTA «Hablá con un asesor» queda a ~200px de este enlace. Con el
        `wa_url` genérico los dos abrían el mismo mensaje: dos caminos a la
        misma acción, que ui.md prohíbe por nombre. Con su propio texto el
        asesor sabe qué le están pidiendo y dejan de ser el mismo camino.

        La ficha va **con** foto a propósito. La primera versión de este test
        usaba el fixture por defecto, que no trae fotos, así que la cabecera
        plana emitía su propio `.falta-link` antes que este y el `re.search`
        comparaba el enlace de las fotos contra el CTA — dos URLs que ya
        difieren por otro motivo. Pasaba verde con el bug puesto: lo mató la
        mutación, no la revisión.
        """
        html = _body(_prop(photo_urls=["/images/remax/E1/1.webp"],
                           bedrooms=None, bathrooms=None, parking=None,
                           total_area_m2=None, construction_state=None))
        assert html.count('class="falta-link"') == 1
        falta = re.search(r'class="falta-link" href="([^"]+)"', html)
        cta = re.search(r'class="btn-wa" href="([^"]+)"', html)
        assert falta is not None and cta is not None
        assert falta.group(1) != cta.group(1)

    @pytest.mark.parametrize("campo,valor", [
        ("bedrooms", 3),
        ("bathrooms", 2),
        ("parking", 1),
        ("total_area_m2", 150.0),
        ("construction_state", "usado"),
    ])
    def test_con_un_solo_dato_la_seccion_se_emite(self, campo, valor):
        """Cada uno de los cinco campos alcanza solo para sostener la sección."""
        vacios = dict.fromkeys(
            ["bedrooms", "bathrooms", "parking", "total_area_m2",
             "construction_state"], None)
        vacios[campo] = valor
        html = _body(_prop(**vacios))
        assert "datos-duros" in html
        assert "falta-datos" not in html


# ---------------------------------------------------------------------------
# F3 — la galería es una tira en todos los anchos
# ---------------------------------------------------------------------------

class TestLaGaleriaDegradaPorCantidad:
    """Cinco de las trece referencias degradan el mosaico segun cuantas fotos
    hay, y **ninguna lo hace duplicando el template**: Onnix —la marca del
    cliente— con `:has(> :last-child:nth-child(N))` puro.

    Aca pesa mas que en ninguna: el 18,1 % de nuestras fichas no tiene ninguna
    foto y muchas de las que tienen, tienen una.

    Verificado en el navegador contra staging el 2026-08-23:

        0 fotos  celular     estado vacio de 300 px, sin boton
        0 fotos  escritorio  estado vacio de 440 px, sin boton
        1 foto   celular     una columna de 350 px, boton «Ver 1 foto»
        1 foto   escritorio  una columna de 920 px, boton «Ver 1 foto»

    El singular del boton no es un detalle: es el affordance de que la galeria
    existe, y Onnix lo conserva incluso con una sola foto.
    """

    def test_el_css_declara_el_caso_de_una_sola_foto(self, fuente):
        assert ":has(> :last-child:nth-child(1))" in fuente, (
            "se fue la regla de la foto unica: con una sola foto el mosaico "
            "vuelve a dejar dos celdas vacias al costado"
        )

    def test_el_css_declara_el_caso_de_dos_fotos(self, fuente):
        assert ":has(> :last-child:nth-child(2))" in fuente

    def test_el_boton_dice_el_singular(self):
        html = _body(_prop(photo_urls=["/images/remax/E1/1.webp"]))
        assert "Ver 1 foto<" in html.replace("\n", "").replace("  ", "") or \
               "Ver 1 foto" in re.sub(r"\s+", " ", html)
        assert "Ver 1 fotos" not in re.sub(r"\s+", " ", html)

    def test_el_boton_dice_el_plural(self):
        html = _body(_prop(photo_urls=["/images/remax/E1/1.webp",
                                       "/images/remax/E1/2.webp"]))
        assert "Ver 2 fotos" in re.sub(r"\s+", " ", html)

    def test_sin_fotos_no_hay_boton_que_no_abra_nada(self):
        """N1 dejo dos botones de lightbox visibles y enfocables SIN EFECTO en
        toda ficha de una sola foto. Sin fotos no puede quedar ninguno."""
        html = _body(_prop(photo_urls=[]))
        assert "galeria-btn" not in html


class TestLaGaleriaNoEmpujaElPrimerDato:
    """A 1280 px la grilla de fotos metía 1.113 px entre la foto y el primer
    dato: los dormitorios caían en y=1.705. Ese es el motivo por el que la
    galería era una tira y no una grilla.

    La composición del 23/08 la volvió un mosaico, y lo que sostiene la misma
    garantía ya no es «que no sea grilla» sino **que su alto esté acotado**:
    10 de 13 referencias lo acotan en píxeles o por relación de aspecto, y
    ninguna de las que lo acota usa `vh`. El rango observado es 400-550 px en
    escritorio y 280-350 en celular (§Conclusión 2).
    """

    def test_el_alto_esta_acotado_y_no_en_vh(self, fuente):
        reglas = re.findall(r"(?<![\w.-])\.galeria-grid\s*\{([^}]*)\}", fuente)
        assert reglas, "desapareció la regla del mosaico"
        alturas = [
            m.group(1)
            for cuerpo in reglas
            for m in re.finditer(r"height:\s*([^;]+);", cuerpo)
        ]
        assert alturas, "el mosaico dejó de declarar su alto"
        for alto in alturas:
            assert "vh" not in alto, (
                f"el alto de la galería vuelve a depender de la pantalla: {alto}"
            )
            assert re.match(r"^\s*\d+px\s*$", alto), alto

    def test_el_alto_esta_en_el_rango_de_las_referencias(self, fuente):
        alturas = [
            int(m.group(1))
            for cuerpo in re.findall(r"(?<![\w.-])\.galeria-grid\s*\{([^}]*)\}", fuente)
            for m in re.finditer(r"height:\s*(\d+)px", cuerpo)
        ]
        assert alturas
        assert min(alturas) <= 350, "el mosaico de celular quedó más alto que las 13"
        assert max(alturas) <= 550, "el mosaico de escritorio se pasó del rango"




# ---------------------------------------------------------------------------
# Los dos números — calculados, nunca copiados
# ---------------------------------------------------------------------------

def _luminancia(hexa: str) -> float:
    r, g, b = (int(hexa[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contraste(a: str, b: str) -> float:
    la, lb = _luminancia(a), _luminancia(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _token(fuente: str, nombre: str) -> str:
    m = re.search(rf"--{nombre}:\s*(#[0-9A-Fa-f]{{6}})\s*;", fuente)
    assert m is not None, f"token --{nombre} no declarado"
    return m.group(1)


class TestNumerosDeLaCabeceraPlana:
    def test_el_enlace_de_la_ausencia_pasa_AA(self, fuente):
        """El enlace es texto normal sobre `--bg` (la cabecera plana no tiene
        overlay ni foto detrás): piso 4,5:1."""
        assert _contraste(_token(fuente, "accent"), _token(fuente, "bg")) >= 4.5

    def test_el_texto_de_la_ausencia_pasa_AA(self, fuente):
        assert _contraste(_token(fuente, "text-sec"), _token(fuente, "bg")) >= 4.5

    def test_el_enlace_de_la_ausencia_llega_a_44px(self, fuente):
        """`ui.md`: touch targets de 44×44. Un `<a>` en línea dentro de un `<p>`
        hereda el alto de la línea (~22px) si no se le dice otra cosa — es el
        mismo agujero que A7 cerró en `.wordmark`."""
        regla = re.search(r"\.falta-link\s*\{(.*?)\}", fuente, re.DOTALL)
        assert regla is not None
        cuerpo = regla.group(1)
        assert "min-height: 44px" in cuerpo
        assert "inline-flex" in cuerpo

    def test_la_cabecera_no_trae_ningun_hex(self, fuente):
        """`ui.md`: cero hex en CSS propio. Los tokens ya están en `:root`."""
        for selector in (r"\.datos-precio\s*\{", r"\.datos-ubicacion\s*\{",
                         r"\.falta\s*\{", r"\.falta-link\s*\{"):
            regla = re.search(selector + r"(.*?)\}", fuente, re.DOTALL)
            assert regla is not None, selector
            assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", regla.group(1)), selector

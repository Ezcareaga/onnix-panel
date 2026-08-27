"""Las reglas de `.claude/rules/ui.md` que la ficha pública rompía.

No son hallazgos de accesibilidad —esos viven en
`test_portal_publico_a11y.py`—, son reglas del proyecto: qué está prohibido en
el hero, cuántas acciones primarias puede haber por vista, qué se navega
por encabezados y cómo se escribe el nombre de la marca. F1, F5, F9 y F11 de
`docs/audit/FICHA_PLAN_20260823.md`.

El markup se lee del **render**, no del archivo: la plantilla tiene las dos
cabeceras (con foto y sin foto) escritas en el mismo texto, así que un assert
por substring sobre la fuente da verde con cualquiera de las dos rota.

El CSS se lee sin comentarios: el comentario que explica por qué se fue la
píldora nombra la píldora.
"""

from __future__ import annotations

import re

import pytest

from tests.plantillas_publicas import fuente_de
from tests.test_ficha_sin_foto import FICHA, _body, _prop

_LISTADO = FICHA.parent / "propiedades.html"


@pytest.fixture(scope="module")
def fuente() -> str:
    return fuente_de(FICHA)


@pytest.fixture(scope="module")
def listado() -> str:
    return fuente_de(_LISTADO)


CON_FOTO = ["/images/remax/E1/1.webp", "/images/remax/E1/2.webp"]


def _location(html: str) -> str | None:
    """El texto de la línea de ubicación, ya renderizado.

    Cambió de `.hero-location` a `.datos-ubicacion` con la recomposición del
    23/08: el bloque salió de encima de la foto. El contenido es el mismo —
    operación, tipo, barrio y ciudad, con los que faltan salteados.
    """
    m = re.search(r'<p class="datos-ubicacion">\s*(.*?)\s*</p>', html, re.DOTALL)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# F1 — el eyebrow chip sale del hero
# ---------------------------------------------------------------------------

class TestSinPildoraEnElHero:
    """`ui.md`, «Prohibido porque parece hecho por IA»: eyebrow chips en el
    hero, por nombre. La ficha tenía uno de 10,4px en versalitas con borde oro
    arriba del `h1` — y solo en la rama con foto: la cabecera sin foto ya decía
    lo mismo en línea con la ubicación.
    """

    def test_el_chip_no_queda_ni_en_el_css_ni_en_el_markup(self, fuente):
        assert "op-chip" not in fuente

    @pytest.mark.parametrize("fotos", [[], CON_FOTO])
    def test_la_operacion_y_el_tipo_van_en_la_linea_de_ubicacion(self, fotos):
        html = _body(_prop(photo_urls=fotos, operation="venta",
                           property_type="casa"))
        assert _location(html) == "Venta · Casa · Zona Aeropuerto · Luque"

    def test_no_hay_dos_cabeceras(self):
        """Antes el dato tenía dos tratamientos según hubiera foto o no: píldora
        en una rama, línea de texto en la otra. La recomposición del 23/08 no
        las emparejó — dejó una sola, afuera del bloque de fotos."""
        for fotos in ([], CON_FOTO):
            html = _body(_prop(photo_urls=fotos))
            assert len(re.findall(r'class="datos-ubicacion"', html)) == 1
        con = _location(_body(_prop(photo_urls=CON_FOTO)))
        sin = _location(_body(_prop(photo_urls=[])))
        assert con == sin

    @pytest.mark.parametrize("campos,esperado", [
        ({"operation": None, "property_type": None},
         "Zona Aeropuerto · Luque"),
        ({"operation": "alquiler", "property_type": None},
         "Alquiler · Zona Aeropuerto · Luque"),
        ({"neighborhood": None}, "Venta · Casa · Luque"),
    ])
    def test_lo_que_falta_no_deja_separadores_sueltos(self, campos, esperado):
        base = {"operation": "venta", "property_type": "casa"}
        html = _body(_prop(photo_urls=CON_FOTO, **{**base, **campos}))
        assert _location(html) == esperado

    def test_sin_ningun_dato_de_contexto_no_se_emite_la_linea(self):
        html = _body(_prop(photo_urls=CON_FOTO, operation=None,
                           property_type=None, neighborhood=None, city=None))
        assert "datos-ubicacion" not in html


# ---------------------------------------------------------------------------
# F5 — una sola acción primaria por ancho
# ---------------------------------------------------------------------------

def _corte(css: str, selector: str, declaracion: str) -> int | None:
    """El `min-width` de la media query donde `selector` declara algo.

    Devuelve el número, no un booleano: los dos cortes de F5 tienen que ser el
    mismo y un assert por presencia no lo notaría.
    """
    for m in re.finditer(r"@media\s*\(\s*min-width:\s*(\d+)px\s*\)\s*\{", css):
        i, prof = m.end(), 1
        while i < len(css) and prof:
            prof += (css[i] == "{") - (css[i] == "}")
            i += 1
        cuerpo = css[m.end():i]
        regla = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", cuerpo)
        if regla and re.search(declaracion, regla.group(1)):
            return int(m.group(1))
    return None


class TestUnaSolaAccionPrimaria:
    """D5 — `ui.md`: «una sola acción primaria por vista» y «nunca dos botones
    que hagan lo mismo». Medido en el navegador: entre 560 y 959px se ven dos
    `.btn-wa` en el DOM, el inline y el de la barra fija.

    **El test NO fija el valor del corte**, y esa es la corrección. La primera
    versión exigía que la barra desapareciera exactamente donde el CTA inline
    pasa a fila (560px). Suena razonable y es peor: el CTA inline vive a unos
    2.600px de scroll, así que apagar la barra a 560 deja todo el rango
    560-959px **sin ningún contacto visible sin scrollear** — y el contacto es
    lo único que esta página tiene que lograr.

    La segunda versión sostenía «la barra nunca se apaga antes de que el bloque
    inline sea fila». Sirvió mientras la barra se apagaba en algún lado. Desde
    el 2026-08-23 **no se apaga en ninguno**: arriba de 960px cambia de forma —
    pierde el fondo, el borde y el texto, y queda la píldora flotante que Ez
    eligió para el fold de escritorio.

    Lo que se sostiene ahora es el invariante que ninguna de las dos versiones
    llegaba a decir: **ningún ancho se queda sin un camino de contacto
    visible**. Es más fuerte que el anterior y no depende de ningún número.
    """

    def test_ningun_ancho_se_queda_sin_camino_de_contacto(self, fuente):
        """A 1280x800 el CTA inline caía en y=1.375 y la barra no existía
        arriba de 960: en escritorio no había ningún contacto sin scrollear."""
        assert _corte(fuente, ".sticky-cta", r"display:\s*none") is None, (
            "`.sticky-cta` vuelve a apagarse en algún ancho, y arriba de ese "
            "corte la ficha se queda sin contacto visible: el CTA inline está "
            "a ~1.375px de scroll"
        )
        base = re.search(r"\.sticky-cta\s*\{([^}]*)\}", fuente)
        assert base and re.search(r"position:\s*fixed", base.group(1)), (
            "`.sticky-cta` dejó de ser fija: deja de estar visible sin scroll"
        )

    def test_donde_la_pildora_deja_de_decir_el_precio_lo_dice_el_cuerpo(self, fuente):
        """El precio es el dato con el que se decide escribir: ningún ancho
        puede quedarse sin él a la vista.

        Esta prueba comparaba dos cortes de viewport —el de la píldora contra
        el del CTA inline— y exigía `pildora >= inline`. Dejó de poder
        compararlos el 2026-08-24: el CTA inline pasó a decidir su dirección
        con un `@container`, porque vive en la columna lateral de 301px y una
        media query no sabe cuánto mide esa columna. Los dos cortes quedaron en
        ejes distintos y la resta perdió sentido.

        Lo que el invariante protegía sigue vivo y ahora se verifica donde
        importa: arriba de los 960px la píldora se queda sin texto, así que el
        precio tiene que estar en el cuerpo. Está —`.datos-precio`, medido en
        producción a 1152x623: top=533, adentro del viewport sin scrollear— y
        ninguna regla lo apaga.
        """
        pildora = _corte(fuente, ".sticky-cta-text", r"display:\s*none")
        assert pildora is not None, (
            "`.sticky-cta-text` ya no se oculta: la píldora de escritorio "
            "volvió a ser una barra de ancho completo"
        )
        assert re.search(r"\.datos-precio\s*\{", fuente), (
            "no existe `.datos-precio`: arriba de "
            f"{pildora}px la píldora no dice el precio y el cuerpo tampoco"
        )
        assert _corte(fuente, ".datos-precio", r"display:\s*none") is None, (
            "`.datos-precio` se oculta en algún ancho. Arriba de "
            f"{pildora}px la píldora ya no dice el precio: si el cuerpo "
            "tampoco, no queda ninguno a la vista"
        )

    def test_la_pildora_de_escritorio_conserva_el_boton(self, fuente):
        """Ocultar `.sticky-cta` entero era el bug; ocultar el botón sería el
        mismo bug escrito de otra forma."""
        assert _corte(fuente, ".sticky-cta .btn-wa", r"display:\s*none") is None, (
            "el botón de la píldora se oculta en algún ancho"
        )

    def test_el_boton_no_espera_300ms_al_doble_tap(self, fuente):
        """El único CTA táctil de la vista. Sin `touch-action: manipulation`
        el navegador retiene el tap por si viene un doble tap de zoom."""
        regla = re.search(r"\.btn-wa\s*\{(.*?)\}", fuente, re.DOTALL)
        assert regla is not None
        assert re.search(r"touch-action:\s*manipulation", regla.group(1))


# ---------------------------------------------------------------------------
# F9 — la ficha se navega por encabezados
# ---------------------------------------------------------------------------

def _encabezados(html: str) -> list[tuple[int, str]]:
    """(nivel, id) de cada encabezado del markup, parseado como tag.

    Por tag y no por línea: `section-label` aparece también en el `<style>` y
    en los `aria-labelledby`, y un conteo por substring cuenta esas.
    """
    return [(int(m.group(1)), m.group(2) or "")
            for m in re.finditer(r"<h([1-6])\b([^>]*)>", html)]


class TestEncabezadosIntermedios:
    """D13 — la ficha tenía **1 `h1` y 0 `h2`**.

    Se navegaba por landmarks pero no por encabezados, que es como la recorre
    un lector de pantalla. Las cuatro secciones se rotulaban con un `<p>` que
    solo servía de `aria-labelledby`. El listado ya tiene 1 `h1` + 24 `h2`.
    """

    @pytest.mark.parametrize("fotos", [[], CON_FOTO])
    def test_hay_un_solo_h1(self, fotos):
        niveles = [n for n, _ in _encabezados(_body(_prop(photo_urls=fotos)))]
        assert niveles.count(1) == 1

    def test_cada_seccion_con_rotulo_visible_tiene_su_h2(self):
        """La regla es «rótulo visible ⇒ h2», y vale en los dos sentidos.

        La recomposición del 23/08 sacó dos rótulos: la galería no lleva
        ninguno —13 de 13 referencias no rotulan las fotos— y los datos duros
        dejaron de ser una sección con encabezado «Características» para pasar
        a ser la fila de specs debajo del título. Quedan los dos que sí se
        rotulan.
        """
        html = _body(_prop(photo_urls=CON_FOTO, latitude=-25.3, longitude=-57.6))
        ids = [i for n, i in _encabezados(html) if n == 2]
        assert len(ids) == 2, "descripción y ubicación"
        for esperado in ("desc-label", "map-label"):
            assert any(esperado in i for i in ids), esperado

    def test_no_quedo_ningun_rotulo_sin_su_encabezado(self):
        """El otro sentido de la regla: `.section-label` es la clase del rótulo
        visible, y sólo puede aparecer sobre un `h2`."""
        html = _body(_prop(photo_urls=CON_FOTO, latitude=-25.3, longitude=-57.6))
        for m in re.finditer(r'<(\w+)[^>]*class="section-label"', html):
            assert m.group(1) == "h2", m.group(0)

    def test_ningun_encabezado_salta_de_nivel(self):
        niveles = [n for n, _ in _encabezados(
            _body(_prop(photo_urls=CON_FOTO, latitude=-25.3, longitude=-57.6)))]
        assert niveles[0] == 1
        for anterior, actual in zip(niveles, niveles[1:]):
            assert actual <= anterior + 1, niveles

    def test_el_rotulo_sigue_siendo_el_nombre_accesible_de_su_seccion(self):
        """El `id` no es decorativo: cada `<section>` lo referencia. Si el
        encabezado cambia de id, la sección queda sin nombre."""
        html = _body(_prop(photo_urls=CON_FOTO, latitude=-25.3, longitude=-57.6))
        for referenciado in re.findall(r'aria-labelledby="([^"]+)"', html):
            assert f'id="{referenciado}"' in html, referenciado


# ---------------------------------------------------------------------------
# F11 — el wordmark deja de gritar
# ---------------------------------------------------------------------------

class TestNombreDeLaMarca:
    """`ui.md`: «Sentence case siempre, nunca Title Case ni MAYÚSCULAS».

    El criterio del benchmark es que `uppercase` vale para etiquetas de
    10-12px con `letter-spacing` y para nada más. `.wordmark` declara 1.1rem
    (17,6px): no es una etiqueta. Los otros cuatro `uppercase` de la ficha
    —`.section-label`, `.mono-label`, `.gallery-label` y el del listado— sí
    califican y se quedan.
    """

    @pytest.mark.parametrize("vista", ["fuente", "listado"])
    def test_el_wordmark_no_va_en_versalitas(self, vista, request):
        css = request.getfixturevalue(vista)
        regla = re.search(r"\.wordmark\s*\{(.*?)\}", css, re.DOTALL)
        assert regla is not None
        assert not re.search(r"text-transform:\s*uppercase", regla.group(1))

    @pytest.mark.parametrize("vista", ["fuente", "listado"])
    def test_la_marca_se_escribe_como_se_escribe(self, vista, request):
        """No alcanza con sacar el `text-transform`: el texto estaba escrito en
        mayúsculas en el markup, así que la regla podía irse sin que cambiara
        nada de lo que se ve."""
        assert "ONNIX" not in request.getfixturevalue(vista)

    def test_las_etiquetas_chicas_conservan_sus_versalitas(self, fuente):
        """La contraprueba: el criterio es el tamaño, no el disgusto con las
        versalitas. Si alguien las borra todas, este test lo dice."""
        # `.gallery-label` se fue con la recomposición del 23/08: la galería
        # dejó de rotularse —13 de 13 referencias no rotulan las fotos— y con
        # ella se fue su etiqueta. Las dos que quedan siguen siendo de 10-12px.
        for clase in (".section-label", ".mono-label"):
            regla = re.search(rf"{re.escape(clase)}\s*\{{(.*?)\}}", fuente,
                              re.DOTALL)
            assert regla is not None, clase
            assert re.search(r"text-transform:\s*uppercase", regla.group(1)), (
                f"{clase} es una etiqueta de 10-12px: ahí uppercase es correcto"
            )

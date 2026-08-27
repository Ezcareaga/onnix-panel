"""La foto de la ficha — partials/property_photo.html.

El bug que cierra: `grep onerror public/*.html` no devolvía nada, así que una
foto faltante se veía como la caja de imagen rota del navegador en la página
que ve el cliente. `photo_url` sale de `local_image_count` en la base y no de
mirar el disco, y esos dos ya divergieron cuando se perdieron las 9,7 GB de
fotos: degradar no es el caso raro.
"""

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
PUBLIC = TEMPLATES / "public"
FICHA = TEMPLATES / "properties" / "detail.html"

# El comentario que explica una prohibición contiene el patrón prohibido. Se
# filtran los comentarios de Jinja antes de assertar, o el test falla contra su
# propia documentación.
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.S)


def _sin_comentarios(path: Path) -> str:
    return _HTML_COMMENT.sub("", _JINJA_COMMENT.sub("", path.read_text(encoding="utf-8")))


def _render(src, alt="", img_class="", img_attrs="", ph_class=""):
    """Renderiza el macro tal como lo llaman el panel y el portal.

    `autoescape=True` no es decoración: es lo que hace Starlette
    (`Jinja2Templates`), y sin eso este archivo no puede ver el bug de
    `img_attrs` — con autoescape apagado el atributo sale bien en el test y
    escapado en producción.
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    tpl = env.from_string(
        '{% from "partials/property_photo.html" import foto %}'
        "{% call foto(src, alt=alt, img_class=img_class, img_attrs=img_attrs,"
        " ph_class=ph_class) %}"
        '<span class="monogram">Onnix</span>'
        "{% endcall %}"
    )
    return tpl.render(src=src, alt=alt, img_class=img_class,
                      img_attrs=img_attrs, ph_class=ph_class)


class TestMacroFoto:
    def test_con_foto_emite_img_con_onerror(self):
        html = _render("/images/onnixpy/51532/1.webp", alt="Casa")
        assert "<img" in html
        assert "onerror=" in html
        assert "/images/onnixpy/51532/1.webp" in html

    def test_con_foto_el_placeholder_va_oculto_pero_presente(self):
        # Presente en el DOM desde el server: el onerror solo lo destapa, no lo
        # inventa. Sin eso el fallback necesitaría construir markup desde JS.
        html = _render("/images/x/1.webp")
        assert 'style="display:none"' in html
        assert "monogram" in html

    def test_el_onerror_devuelve_el_display_a_la_hoja_de_estilos(self):
        # `display=''` y NO `display='flex'`: el partial no puede saber si quien
        # lo llama centra con flex, con grid o con nada.
        html = _render("/images/x/1.webp")
        assert "this.nextElementSibling.style.display=''" in html
        assert "style.display='flex'" not in html

    def test_sin_foto_no_hay_img_y_el_placeholder_se_ve(self):
        html = _render(None)
        assert "<img" not in html
        assert "monogram" in html
        assert "display:none" not in html

    def test_el_placeholder_es_decorativo(self):
        assert 'aria-hidden="true"' in _render(None)

    def test_la_clase_del_placeholder_la_pone_quien_llama(self):
        # El portal usa .photo-placeholder / .hero-placeholder; el panel, utilities.
        assert 'class="photo-placeholder"' in _render(None, ph_class="photo-placeholder")

    def test_img_attrs_llega_como_atributos_y_no_como_texto(self):
        """`img_attrs` es markup, y el autoescape lo estaba convirtiendo en texto.

        `properties_table.html` pasa `' loading="lazy"'` desde que existe el
        macro: sin `| safe` salía `loading=&#34;lazy&#34;`, o sea el atributo no
        existía y ninguna miniatura de la tabla cargaba diferida. No se veía
        porque la foto se dibuja igual.
        """
        html = _render("/images/x/1.webp", img_attrs=' loading="lazy" id="hero-ssr"')
        assert 'loading="lazy"' in html
        assert 'id="hero-ssr"' in html
        assert "&#34;" not in html

    def test_el_resto_de_los_parametros_sigue_escapando(self):
        """`| safe` va SOLO en img_attrs. Lo que puede venir de la base, no."""
        html = _render("/images/x/1.webp", alt='Casa "grande" <b>')
        assert "<b>" not in html
        assert "&lt;b&gt;" in html


class TestPortalNoMuestraCajasRotas:
    """Ningún <img> con src en el portal público puede quedar sin degradación."""

    def test_todo_img_con_src_tiene_onerror(self):
        offenders = []
        for path in sorted(PUBLIC.glob("*.html")):
            for tag in _IMG_TAG.findall(_sin_comentarios(path)):
                if "src=" in tag and "onerror=" not in tag:
                    offenders.append(f"{path.name}: {tag[:80]}")
        assert not offenders, "img sin onerror en el portal público: " + "; ".join(offenders)

    def test_las_fotos_de_la_ficha_pasan_por_el_macro(self):
        """El mosaico del 23/08 no saca la foto rota: le pone el monograma.

        La tira vieja se auto-removía —cada miniatura rota se iba sola y, si se
        iban todas, la sección entera con su contador— porque emitía el `<img>`
        a mano. El mosaico usa el macro, que es la definición única del
        fallback: emite el placeholder oculto y `onerror` lo destapa. La
        garantía de fondo es la misma —el navegador nunca dibuja su caja de
        imagen rota— y de paso deja de haber dos maneras de degradar en el
        mismo archivo.
        """
        fuente = _sin_comentarios(PUBLIC / "property.html")
        assert "galeria-foto" in fuente
        assert "ph_class=\"galeria-placeholder\"" in fuente
        # Y el `<img>` a mano no volvió: si vuelve, vuelve a divergir.
        tags = [t for t in _IMG_TAG.findall(fuente) if "galeria" in t]
        assert tags == [], f"la galería volvió a emitir un <img> propio: {tags}"

    def test_el_fallback_no_esta_copiado_en_las_plantillas_del_portal(self):
        # Una sola definición: partials/property_photo.html. Si vuelve a
        # aparecer copiada, vuelve a divergir.
        copias = [
            p.name
            for p in sorted(PUBLIC.glob("*.html"))
            if "nextElementSibling" in _sin_comentarios(p)
        ]
        assert not copias, f"el fallback volvió a copiarse en {copias}"


class TestFichaDelPanelNoMuestraCajasRotas:
    """La galería de `/properties/{id}` cuando las fotos no cargan.

    Quedaba un rectángulo negro de 460px: ni el hero SSR ni el carousel de
    Alpine tenían degradación, y en un `x-for` no alcanza con agregar un
    atributo — hay que decidir qué hace el componente con el array. La decisión
    es que `photos` es el modelo: una URL que da 404 se saca del array, y de
    ahí salen solos el contador veraz, la miniatura que desaparece y el estado
    vacío cuando se cae la última.
    """

    def test_toda_img_de_la_galeria_degrada(self):
        """Ni una `<img>` de la ficha puede quedar sin qué hacer si falla.

        Las que escribe la plantilla llevan `@error`; las que emite el macro
        `foto()` llevan su `onerror` y las cubre `TestMacroFoto`.
        """
        offenders = [
            tag[:90]
            for tag in _IMG_TAG.findall(_sin_comentarios(FICHA))
            if "@error=" not in tag and "onerror=" not in tag
        ]
        assert not offenders, "img sin degradación en la ficha: " + "; ".join(offenders)

    def test_ninguna_img_de_la_ficha_se_queda_sin_alt(self):
        offenders = [
            tag[:90]
            for tag in _IMG_TAG.findall(_sin_comentarios(FICHA))
            if "alt=" not in tag and ":alt=" not in tag
        ]
        assert not offenders, "img sin alt: " + "; ".join(offenders)

    def test_las_fotos_que_emite_el_macro_tambien_van_con_alt(self):
        """El macro siempre escribe `alt`, así que sin este assert una llamada
        que se olvide del argumento sale con `alt=""` y el test de arriba no la
        ve: el `<img>` no está en esta plantilla, lo emite `foto()`.
        """
        html = _sin_comentarios(FICHA)
        llamadas = re.findall(r"\{%\s*call foto\((.*?)\)\s*%\}", html, re.S)
        assert llamadas, "la ficha dejó de usar el macro foto()"
        sin_alt = [c[:60] for c in llamadas if "alt=" not in c]
        assert not sin_alt, "call foto() sin alt: " + "; ".join(sin_alt)

    def test_el_error_saca_la_foto_del_array_y_no_solo_la_esconde(self):
        html = _sin_comentarios(FICHA)
        assert '@error="drop(url)"' in html
        # `drop` opera sobre el array, no sobre el DOM: es lo que hace que el
        # contador y la tira de miniaturas queden consistentes sin tocarlos.
        assert "this.photos.splice(" in html

    def test_drop_reencuadra_el_indice_cuando_cae_la_foto_que_se_estaba_viendo(self):
        # Sin esto, sacar la última deja `idx` apuntando fuera del array y el
        # carousel no muestra ningún slide: rectángulo negro de nuevo.
        assert "Math.max(this.photos.length - 1, 0)" in _sin_comentarios(FICHA)

    def test_cuando_se_caen_todas_aparece_el_estado_vacio(self):
        assert 'x-show="!photos.length"' in _sin_comentarios(FICHA)

    def test_las_miniaturas_no_van_diferidas(self):
        """Medido en Chrome: una `<img loading="lazy">` dentro de un
        `overflow-x-auto` no llega a cargar nunca, y sin cargar tampoco falla.
        La tira es lo que dispara `drop()`, así que tiene que pedir las fotos.
        """
        html = _sin_comentarios(FICHA)
        tira = html[html.index('aria-label="Miniaturas de la galería"'):]
        tira = tira[: tira.index("</template>")]
        assert "loading=" not in tira, tira

    def test_el_estado_vacio_esta_escrito_una_sola_vez(self):
        """Cuatro caminos llegan a «no hay foto»; los cuatro tienen que verse igual.

        `ui.md` pide un solo patrón para los estados vacíos. Acá eso es un macro
        de la propia plantilla, no cuatro bloques copiados que se desincronizan.
        """
        html = _sin_comentarios(FICHA)
        assert "{% macro sin_foto()" in html
        assert html.count("Sin fotos disponibles") == 1

    def test_la_ficha_no_usa_el_side_tab_de_borde_acentuado(self):
        # `border-t-2 border-onnix-accent` sobre una card `rounded-xl`: el audit lo
        # marca como el tell más reconocible de una UI generada.
        assert "border-t-2" not in _sin_comentarios(FICHA)

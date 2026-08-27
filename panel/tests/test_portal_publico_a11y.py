"""Accesibilidad medible de las dos vistas públicas del portal.

Origen: `docs/audit/PORTAL_PUBLICO_20260822.md`, la primera corrida de
`/impeccable audit` sobre `/propiedades` y `/prop/{ref}`.

Estos tests **calculan** el ratio de contraste desde los tokens declarados en
cada plantilla; no leen el número de un comentario. Ya pasó en este repo que
dos números escritos a mano decían 5,79 y 2,89 y en realidad eran 11,30 y
5,65.

Y por eso mismo el CSS se lee **sin comentarios**: los comentarios de estas
plantillas nombran el valor viejo (`#fff`, `opacity: 0.55`) al explicar por qué
se fue, así que un assert por substring sobre el archivo crudo pasaría verde
con el bug puesto.
"""
import re
from pathlib import Path

import pytest

from tests.plantillas_publicas import con_includes, sin_comentarios

_PUBLIC = Path(__file__).parent.parent / "app/templates/public"
FICHA = _PUBLIC / "property.html"
LISTADO = _PUBLIC / "propiedades.html"
NOT_FOUND = _PUBLIC / "404.html"

_COMENTARIO_CSS = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMENTARIO_JINJA = re.compile(r"\{#.*?#\}", re.DOTALL)


def _css(path: Path) -> str:
    """El contenido de la plantilla, con sus includes, sin comentarios.

    Resolver el include no es un detalle: desde el 2026-08-23 lo que la ficha y
    el listado comparten —las dos caras de Outfit, once tokens, el reset y el
    header con su wordmark— vive en `public/_estilos_comunes.html`. Leyendo solo
    `property.html` estos tests dejan de ver la mitad de la CSS que el navegador
    sí recibe.
    """
    return sin_comentarios(con_includes(path))


@pytest.fixture(scope="module")
def ficha() -> str:
    return _css(FICHA)


@pytest.fixture(scope="module")
def listado() -> str:
    return _css(LISTADO)


@pytest.fixture(scope="module")
def pagina_404() -> str:
    return _css(NOT_FOUND)


def _luminancia(hexa: str) -> float:
    r, g, b = (int(hexa[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contraste(a: str, b: str) -> float:
    la, lb = _luminancia(a), _luminancia(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _token(css: str, nombre: str) -> str:
    m = re.search(rf"--{nombre}:\s*(#[0-9A-Fa-f]{{6}})", css)
    assert m, f"falta el token --{nombre}"
    return m.group(1)


def _regla(css: str, selector: str) -> str:
    """TODO lo que un selector declara, no la primera vez que aparece.

    Desde que el preámbulo común salió a `_estilos_comunes.html`, un selector
    puede estar declarado dos veces: la base en el partial y lo propio en la
    plantilla. `body` es el caso — la base está en el partial y el
    `padding-bottom` que reserva el hueco de la barra fija, en la ficha.

    Con `re.search` ganaba la primera y el test leía media regla. El navegador
    ve las dos, así que el test también.
    """
    cuerpos = re.findall(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
    assert cuerpos, f"no se pudo leer {selector}"
    return "\n".join(cuerpos)


def _color_declarado(css: str, cuerpo: str) -> str:
    """El `color:` de una regla, resuelto a hex venga por token o literal."""
    m = re.search(r"\bcolor:\s*var\(--([\w-]+)\)", cuerpo)
    if m:
        return _token(css, m.group(1))
    m = re.search(r"\bcolor:\s*(#[0-9A-Fa-f]{6})", cuerpo)
    assert m, "la regla no declara un color legible"
    return m.group(1)


class TestBotonWhatsApp:
    """A1 — el único camino de conversión de todo el portal.

    Medía 1,98:1 en reposo y 2,45:1 en hover: texto blanco sobre el verde de
    WhatsApp, 14,4px con peso 600. Es texto normal, así que el piso es 4,5:1 y
    no hay eximente de texto grande. El verde se queda —`ui.md` lo declara—;
    lo que cambió es la tinta encima.
    """

    def test_el_texto_se_lee_en_reposo(self, ficha: str):
        color = _color_declarado(ficha, _regla(ficha, ".btn-wa"))
        ratio = _contraste(color, _token(ficha, "wa-green"))
        assert ratio >= 4.5, f"el CTA de WhatsApp da {ratio:.2f}:1 en reposo"

    def test_el_texto_se_lee_en_hover(self, ficha: str):
        """El hover oscurece el fondo; si la tinta es clara, empeora ahí."""
        color = _color_declarado(ficha, _regla(ficha, ".btn-wa"))
        ratio = _contraste(color, _token(ficha, "wa-green-hv"))
        assert ratio >= 4.5, f"el CTA de WhatsApp da {ratio:.2f}:1 en hover"


class TestBuscadorDelListado:
    """A4 — el placeholder es la única pista de qué hace cada campo."""

    def test_el_placeholder_se_lee(self, listado: str):
        cuerpo = _regla(listado, ".field input::placeholder")
        color = _color_declarado(listado, cuerpo)
        ratio = _contraste(color, _token(listado, "surface-2"))
        assert ratio >= 4.5, f"el placeholder del buscador da {ratio:.2f}:1"

    def test_el_placeholder_no_se_apaga_con_opacidad(self, listado: str):
        """`opacity` compone el color contra el fondo y baja el ratio sin que
        el token cambie: el test de arriba mediría el token y no lo que se ve.
        `ui.md` además prohíbe señalar estado con opacidad."""
        cuerpo = _regla(listado, ".field input::placeholder")
        assert "opacity" not in cuerpo, (
            "el placeholder no puede llevar opacity: el ratio que mide el test "
            "de arriba dejaría de ser el que ve el visitante"
        )


class TestAreaTactilDelWordmark:
    """A7 — el wordmark es el único link al home de las dos vistas, y medía
    170x28,2px contra el piso de 44x44 que pide `ui.md`."""

    @pytest.mark.parametrize("vista", ["ficha", "listado"])
    def test_el_link_al_home_llega_al_piso_tactil(self, vista, request):
        css = request.getfixturevalue(vista)
        cuerpo = _regla(css, ".wordmark")
        m = re.search(r"min-height:\s*(\d+)px", cuerpo)
        assert m, f".wordmark de {vista} no declara min-height"
        assert int(m.group(1)) >= 44, (
            f".wordmark de {vista} declara {m.group(1)}px, el piso es 44"
        )
        assert re.search(r"display:\s*inline-flex", cuerpo), (
            f".wordmark de {vista} necesita ser flex para que el min-height "
            "agrande el área y no solo la caja del texto"
        )


class TestLightbox:
    """A3 y A6 — la galería se declara aria-modal pero dejaba 17 elementos
    tabulables detrás, y apagaba prev/next con opacidad."""

    def test_el_fondo_queda_inerte_mientras_esta_abierto(self, ficha: str):
        assert 'setAttribute(\'inert\'' in ficha or 'setAttribute("inert"' in ficha, (
            "el lightbox se declara aria-modal: con el teclado no se puede "
            "salir de la galería sin cerrarla"
        )
        assert "removeAttribute('inert'" in ficha or 'removeAttribute("inert"' in ficha, (
            "el inert tiene que sacarse al cerrar, o la página queda muerta"
        )

    def test_los_controles_de_navegacion_no_se_apagan_con_opacidad(self, ficha: str):
        """Con una sola foto, prev/next quedaban :disabled a 1,70:1."""
        assert not re.search(r"btn(Prev|Next)\.disabled\s*=", ficha), (
            "prev/next con una sola foto se ocultan; :disabled los dejaba "
            "visibles a 1,70:1, y ui.md prohíbe señalar estado con opacidad"
        )


class TestBordeDeLosCampos:
    """A5 — un borde de control se separa de su fondo por 3:1 (WCAG 1.4.11).

    `--border` daba 1,28:1 contra el panel, y el relleno del campo contra el
    panel da 1,06:1: sin un borde legible los cinco campos del buscador no
    tienen límite perceptible por ninguna de las dos vías del criterio.
    """

    def test_el_borde_del_campo_se_ve_contra_el_panel(self, listado: str):
        cuerpo = _regla(listado, ".field select,\n        .field input")
        m = re.search(r"border:\s*1px solid var\(--([\w-]+)\)", cuerpo)
        assert m, "el campo del buscador tiene que declarar su borde por token"
        borde = _token(listado, m.group(1))
        for fondo in ("surface-1", "surface-2"):
            ratio = _contraste(borde, _token(listado, fondo))
            assert ratio >= 3.0, (
                f"el borde del campo da {ratio:.2f}:1 contra --{fondo}"
            )

    def test_el_token_no_diverge_del_de_la_landing(self, listado: str):
        """El portal y la landing definen los mismos nueve colores con nombres
        distintos, y ya divergieron dos veces. Este token se copió de la
        landing a propósito; si alguna de las dos lo mueve, que se entere."""
        landing = (
            Path(__file__).parent.parent.parent
            / "landing/assets/css/styles.css"
        )
        if not landing.exists():
            pytest.skip(
                "falta landing/assets/css/styles.css — sin él no hay contra qué "
                "comparar el token"
            )
        m = re.search(
            r"--border-control:\s*(#[0-9A-Fa-f]{6})",
            landing.read_text(encoding="utf-8"),
        )
        assert m, "la landing dejó de declarar --border-control"
        assert _token(listado, "border-control").lower() == m.group(1).lower(), (
            "el --border-control del portal y el de la landing divergieron"
        )


class TestReflowDelHeader:
    """A2 — a 200 % de texto el ancho de scroll se iba a 473 px (listado) y
    663 px (ficha) en un viewport de 390 px."""

    @pytest.mark.parametrize("vista", ["ficha", "listado"])
    def test_el_header_crece_con_el_texto(self, vista, request):
        cuerpo = _regla(request.getfixturevalue(vista), ".header-inner")
        assert not re.search(r"(?<!min-)height:\s*\d+px", cuerpo), (
            f".header-inner de {vista} no puede fijar height: a 200% el "
            "wordmark mide 112,6px y queda recortado"
        )
        assert re.search(r"min-height:\s*\d+px", cuerpo), (
            f".header-inner de {vista} tiene que declarar min-height"
        )


class TestScrollPadding:
    """D15 — el header es sticky de 56 px: sin scroll-padding un salto a un
    ancla deja el objetivo tapado (WCAG 2.4.11)."""

    @pytest.mark.parametrize("vista", ["ficha", "listado"])
    def test_el_ancla_no_queda_abajo_del_header(self, vista, request):
        css = request.getfixturevalue(vista)
        m = re.search(r"scroll-padding-top:\s*(\d+)px", css)
        assert m, f"{vista} no declara scroll-padding-top"
        alto = re.search(r"min-height:\s*(\d+)px", _regla(css, ".header-inner"))
        assert alto, "no se pudo leer el alto del header"
        assert int(m.group(1)) >= int(alto.group(1)), (
            f"el scroll-padding de {vista} ({m.group(1)}px) es menor que el "
            f"header ({alto.group(1)}px)"
        )


class TestTipografiaSelfHosteada:
    """Tanda 2 — Cormorant Garamond fuera, Outfit self-hosteada.

    El portal pedia dos familias al CDN de Google: 313,3 KB declarados,
    168,3 KB bajados, 33 @font-face y dos origenes de terceros bloqueando el
    render — con 94,5 KB de Outfit que eran duplicado exacto de
    /static/fonts/outfit-latin.woff2, que ya es variable 100-900 y pesa
    32,2 KB. Tres caras de Cormorant se pedian y no se usaban nunca.
    """

    @pytest.mark.parametrize("vista", ["ficha", "listado", "pagina_404"])
    def test_ninguna_fuente_viene_de_un_tercero(self, vista, request):
        css = request.getfixturevalue(vista)
        for host in ("fonts.googleapis.com", "fonts.gstatic.com",
                     "fonts.bunny.net", "use.typekit.net"):
            assert host not in css, (
                f"{vista} sigue pidiendo tipografia a {host}"
            )

    @pytest.mark.parametrize("vista", ["ficha", "listado", "pagina_404"])
    def test_outfit_se_sirve_desde_el_propio_dominio(self, vista, request):
        css = request.getfixturevalue(vista)
        assert "/static/fonts/outfit-latin.woff2" in css, (
            f"{vista} tiene que declarar el @font-face self-hosteado"
        )
        # Variable 100-900: una sola cara cubre todos los pesos. Si alguien la
        # parte en caras por peso vuelve el problema que esto elimina.
        #
        # Se comprueba bloque por bloque, no con un search sobre todo el CSS:
        # hay DOS @font-face (latin y latin-ext) y un search global se conforma
        # con que uno solo sea variable. Ese test pasaba verde con la mitad
        # rota.
        bloques = re.findall(r"@font-face\s*\{([^}]*)\}", css)
        assert len(bloques) == 2, (
            f"{vista} declara {len(bloques)} @font-face; se esperan 2 "
            "(latin y latin-ext)"
        )
        for i, bloque in enumerate(bloques):
            assert re.search(r"font-weight:\s*100 900", bloque), (
                f"el @font-face #{i + 1} de {vista} no es la cara variable "
                "100-900"
            )

    @pytest.mark.parametrize("vista", ["ficha", "listado", "pagina_404"])
    def test_no_queda_un_token_serif_que_apunta_a_una_sans(self, vista, request):
        """--serif quedaria mintiendo: el nombre dice serif y la familia no lo
        es. Los 7 usos de cada vista pasaron a --sans."""
        css = request.getfixturevalue(vista)
        assert "--serif" not in css, (
            f"{vista} conserva --serif; si Cormorant se fue, el token tambien"
        )
        assert "Cormorant" not in css, f"{vista} sigue nombrando Cormorant"


class TestPoliticaDeSeguridad:
    """La CSP tuvo que abrirse a dos origenes de terceros solo por Cormorant."""

    def test_la_csp_no_permite_fuentes_de_terceros(self):
        main = (
            Path(__file__).parent.parent / "app/main.py"
        ).read_text(encoding="utf-8")
        # Sin comentarios: el que explica este mismo cambio nombra los dos
        # hosts al contar por que se fueron.
        codigo = re.sub(r"^\s*#.*$", "", main, flags=re.MULTILINE)
        for host in ("fonts.googleapis.com", "fonts.gstatic.com"):
            assert host not in codigo, (
                f"la CSP sigue permitiendo {host}; ningun template lo usa"
            )


class TestControlesOcultosDelLightbox:
    """N1 — la regresión que dejó A6 peor que antes.

    `btnPrev.hidden = true` es correcto en JS, pero `[hidden]` de la hoja del
    user-agent es `display: none` con especificidad 0-1-0, y `.lb-btn` declara
    `display: inline-flex` con 0-1-0 también: gana la que viene después, que es
    la de la página. Resultado medido: con una sola foto los botones quedaban
    visibles (109×44 y 114×44), enfocables por teclado y sin efecto — peor que
    el `:disabled` que vinieron a reemplazar, porque al menos aquel se
    anunciaba como deshabilitado (WCAG 4.1.2).
    """

    def test_hidden_realmente_esconde(self, ficha: str):
        cuerpo = _regla(ficha, ".lb-btn[hidden]")
        assert re.search(r"display:\s*none", cuerpo), (
            ".lb-btn[hidden] tiene que declarar display:none; sin eso el "
            "display:inline-flex de .lb-btn gana y el botón se ve igual"
        )

    def test_la_regla_va_antes_de_la_que_pisa(self, ficha: str):
        """Misma especificidad: gana la última. Si alguien mueve el bloque
        arriba de `.lb-btn`, el fix deja de funcionar sin que se note."""
        oculto = ficha.index(".lb-btn[hidden]")
        base = ficha.index(".lb-btn {")
        assert oculto < base, (
            ".lb-btn[hidden] tiene que ir ANTES de .lb-btn, o el "
            "display:inline-flex de la segunda lo pisa"
        )


class TestDesbordeDelCtaDeLaFicha:
    """A2 en la ficha — a 200 % de texto en 390 px desbordaba 58 px y el
    `.btn-wa` quedaba fuera de pantalla. `.cta-block` metía 407 px de contenido
    en una caja de 308."""

    def test_el_bloque_puede_encogerse(self, ficha: str):
        cuerpo = _regla(ficha, ".cta-block")
        assert re.search(r"min-width:\s*0", cuerpo), (
            "sin min-width:0 un flex item no baja de su min-content y el "
            "contenido empuja la página"
        )

    def test_el_boton_no_supera_a_su_caja(self, ficha: str):
        cuerpo = _regla(ficha, ".btn-wa")
        assert re.search(r"max-width:\s*100%", cuerpo), (
            ".btn-wa tiene que estar limitado a su contenedor"
        )
        assert re.search(r"flex-wrap:\s*wrap", cuerpo), (
            "sin wrap, el ícono y la etiqueta son una línea indivisible que a "
            "200% no entra en 390px"
        )


class TestIconoDelBotonDeWhatsApp:
    """El ícono no puede quedarse con una tinta propia.

    A1 cambió el `color` del botón a `--bg`, pero el SVG traía `fill: #fff`
    y no `currentColor`: quedó etiqueta negra e ícono blanco sobre el mismo
    verde, el ícono a 1,98:1 contra los 9,98:1 del texto. Heredando el color
    no puede volver a divergir de la etiqueta que acompaña.
    """

    def test_el_icono_hereda_el_color_del_boton(self, ficha: str):
        cuerpo = _regla(ficha, ".btn-wa-icon")
        assert re.search(r"fill:\s*currentColor", cuerpo), (
            "el ícono tiene que heredar con currentColor; una tinta propia "
            "vuelve a divergir la próxima vez que cambie el botón"
        )
        assert not re.search(r"fill:\s*#[0-9A-Fa-f]{3,8}", cuerpo), (
            "ui.md no admite un hex literal en el CSS"
        )


class TestPrecioDeLaBarraFija:
    """N3 — el precio de la barra fija se recortaba con un ellipsis.

    Medido en el navegador a 390px con el texto al 200 %: `.sticky-price`
    tenía 53px de caja para 230px de contenido. WCAG 1.4.4 pide que el texto
    escale al 200 % sin perder contenido ni funcionalidad, y acá lo que se
    perdía era el precio — el dato con el que se decide si escribir o no.
    """

    def test_el_precio_envuelve_en_vez_de_recortarse(self, ficha: str):
        cuerpo = _regla(ficha, ".sticky-price")
        for declaracion in (r"white-space:\s*nowrap",
                            r"text-overflow:\s*ellipsis",
                            r"overflow:\s*hidden"):
            assert not re.search(declaracion, cuerpo), (
                f".sticky-price declara `{declaracion}`: a 200 % el precio "
                "vuelve a desaparecer detrás del recorte"
            )

    def test_la_barra_deja_bajar_el_boton_a_su_propia_linea(self, ficha: str):
        """Sacar el ellipsis no alcanza: `.sticky-cta .btn-wa` no se encoge
        (`flex-shrink: 0`) y a 200 % se queda con 224,7px de los 310
        disponibles. Medido: sin estas dos declaraciones el precio sigue
        entrando en 53px, ahora sin siquiera el ellipsis que avisaba."""
        assert re.search(r"flex-wrap:\s*wrap", _regla(ficha, ".sticky-cta")), (
            "sin wrap el botón no baja de línea y el precio se queda con lo "
            "que sobra"
        )
        assert not re.search(r"min-width:\s*0", _regla(ficha, ".sticky-cta-text")), (
            "`min-width: 0` era el complemento del ellipsis: deja al bloque "
            "aplastarse por debajo de su min-content en vez de pedir su lugar"
        )

    def test_el_hueco_de_la_barra_esta_abajo_del_footer(self, ficha: str):
        """La barra es `position: fixed` y el footer es lo último del
        documento: reservar el hueco en `.main` deja el footer tapado igual,
        y el precio envolviendo hace la barra más alta."""
        cuerpo = _regla(ficha, "body")
        m = re.search(r"padding-bottom:\s*([\d.]+)rem", cuerpo)
        assert m, (
            "el hueco de la barra fija tiene que reservarlo `body`: es el "
            "único elemento que termina después del footer"
        )
        assert float(m.group(1)) >= 8, (
            f"{m.group(1)}rem no alcanza: medida a 390px con el texto al "
            "200 %, la barra con el precio en su propia línea ocupa 7,95rem"
        )

    def test_el_hueco_se_mide_en_rem(self, ficha: str):
        """En px el hueco queda fijo mientras la barra crece con el zoom de
        texto, que es exactamente el caso que N3 destapó."""
        cuerpo = _regla(ficha, "body")
        assert not re.search(r"padding-bottom:\s*\d+px", cuerpo)


def _rgb(hexa: str) -> tuple[float, float, float]:
    return tuple(int(hexa[i:i + 2], 16) for i in (1, 3, 5))


def _componer(frente: tuple, alfa: float, fondo: tuple) -> tuple:
    """El color que se ve cuando `frente` va con `alfa` sobre `fondo`."""
    return tuple(frente[i] * alfa + fondo[i] * (1 - alfa) for i in range(3))


def _luminancia_rgb(rgb: tuple) -> float:
    def lin(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2])


def _contraste_rgb(a: tuple, b: tuple) -> float:
    la, lb = _luminancia_rgb(a), _luminancia_rgb(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


class TestBordesDelLightbox:
    """D9 y D6 — el borde de los controles de la galería.

    Los dos bordes eran `--accent` copiado a mano con alfa (`rgba(200, 169, 81,
    0.35)` y `0.25`): además de ser un literal que `ui.md` no admite, mover el
    token habría dejado la galería con el acento viejo y nadie se habría enterado.

    El ratio **se calcula** acá, componiendo el borde sobre el fondo real del
    overlay. El número del plan (3,26:1) se recalculó y da 3,27; los de hoy
    (1,91 y 1,48) también. Ninguno se copia: si alguien baja el alfa, este test
    lo dice.
    """

    def _fondo_del_overlay(self, ficha: str) -> tuple:
        cuerpo = _regla(ficha, ".lb-overlay")
        m = re.search(r"background:\s*rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)",
                      cuerpo)
        assert m, "no se pudo leer el fondo del overlay"
        tinta = tuple(int(m.group(i)) for i in (1, 2, 3))
        return _componer(tinta, float(m.group(4)), _rgb(_token(ficha, "bg")))

    def _alfa_del_borde(self, ficha: str) -> float:
        m = re.search(
            r"--border-lb:\s*color-mix\(in srgb,\s*var\(--accent\)\s*(\d+)%",
            ficha)
        assert m, (
            "--border-lb tiene que salir de --accent por color-mix: un literal "
            "vuelve a divergir del token"
        )
        return int(m.group(1)) / 100

    def test_el_borde_se_ve_contra_el_fondo_del_overlay(self, ficha: str):
        fondo = self._fondo_del_overlay(ficha)
        borde = _componer(_rgb(_token(ficha, "accent")),
                          self._alfa_del_borde(ficha), fondo)
        ratio = _contraste_rgb(borde, fondo)
        assert ratio >= 3.0, (
            f"el borde del lightbox da {ratio:.2f}:1 contra el overlay; "
            "WCAG 1.4.11 pide 3:1 para el límite de un control"
        )

    def test_los_dos_controles_usan_el_mismo_token(self, ficha: str):
        assert re.search(r"border:\s*1px solid var\(--border-lb\)",
                         _regla(ficha, ".lb-btn"))
        cerrar = _regla(ficha, ".lb-btn-close")
        assert "border-color" not in cerrar, (
            "el botón de cerrar tenía su propio alfa, más bajo que el de "
            "prev/next: dos bordes del mismo lightbox con dos contrastes"
        )



class TestMovimientoReducido:
    """D4 — la ficha no respetaba `prefers-reduced-motion`.

    Tenía **cero** bloques y tres transiciones, más `scroll-behavior: smooth`,
    que es lo que más se mueve en toda la página. El bloque global de
    `static/css/custom.css` existe, pero esta vista no carga esa hoja: la ficha
    es autocontenida, así que su bloque tiene que estar adentro.
    """

    def test_hay_exactamente_un_bloque_y_es_de_reduce(self, ficha: str):
        bloques = re.findall(
            r"@media\s*\(\s*prefers-reduced-motion:\s*(\w[\w-]*)\s*\)", ficha)
        assert bloques.count("reduce") == 1, (
            "ui.md pide un solo bloque global; varios se desincronizan"
        )
        assert "no-preference" not in bloques, (
            "`no-preference` es opt-in: deja afuera todo lo que se agregue "
            "después fuera del bloque. Es el error que ya está en el listado"
        )

    def test_el_bloque_va_al_final(self, ficha: str):
        """Misma especificidad que las reglas que pisa: gana la última."""
        i = ficha.index("prefers-reduced-motion")
        assert "@media" not in ficha[i:ficha.index("</style>", i)], (
            "hay media queries después del bloque de reduced-motion"
        )

    def test_el_bloque_apaga_las_transiciones_y_el_scroll(self, ficha: str):
        i = ficha.index("prefers-reduced-motion")
        cuerpo = ficha[i:ficha.index("</style>", i)]
        for declaracion in (r"transition-duration:\s*0\.01ms\s*!important",
                            r"animation-duration:\s*0\.01ms\s*!important",
                            r"scroll-behavior:\s*auto\s*!important"):
            assert re.search(declaracion, cuerpo), declaracion

    def test_afuera_del_bloque_el_movimiento_sigue_ahi(self, ficha: str):
        """La prueba negativa: si el bloque apagara todo siempre, el test de
        arriba pasaría verde con la página muerta."""
        antes = ficha[:ficha.index("prefers-reduced-motion")]
        assert re.search(r"scroll-behavior:\s*smooth", antes)
        duraciones = [
            float(v) * (1 if u == "ms" else 1000)
            for v, u in re.findall(r"transition:[^;]*?(\d*\.?\d+)(ms|s)\b", antes)
        ]
        assert duraciones, "la ficha dejó de declarar transiciones"
        assert all(150 <= d <= 200 for d in duraciones), (
            f"ui.md pide 150-200ms: {duraciones}"
        )

class TestNingunTokenEscritoAMano:
    """D6 — un color literal es un color que nadie va a encontrar cuando haya
    que mover la paleta.

    No se buscan literales «feos» a ojo: se leen los tokens de `:root`, se
    convierten a su tripleta RGB y se busca **esa** tripleta escrita como
    `rgba(...)` en cualquier declaración. Así el test sigue sirviendo cuando la
    paleta cambie, y encuentra el caso que importa —el token copiado a mano con
    un alfa encima, que es como estaban el acento del lightbox (`rgba(200, 169,
    81, …)`) y el fondo del header y de la barra fija (`rgba(10, 10, 10, …)`).

    Lo que **no** cubre, a propósito: los negros de las sombras y del scrim del
    lightbox. Son negro puro, no `--bg`, y no hay token de sombra que inventar.
    """

    @pytest.mark.parametrize("vista", ["ficha", "listado"])
    def test_ningun_token_aparece_como_literal(self, vista, request):
        css = request.getfixturevalue(vista)
        copiados = []
        for nombre, valor in re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", css):
            r, g, b = (int(valor[i:i + 2], 16) for i in (1, 3, 5))
            if re.search(rf"rgba?\(\s*{r},\s*{g},\s*{b}\b", css):
                copiados.append(f"--{nombre} ({valor})")
        assert not copiados, (
            f"{vista}: estos tokens están escritos a mano en alguna "
            f"declaración: {copiados}"
        )


class TestAreaTactilDeLaBarraDeFiltros:
    """M2 — «Limpiar» era el unico control bajo 44x44 de las tres superficies
    publicas.

    Medido el 2026-08-23 con `elementFromPoint` sobre las tres paginas
    renderizadas, a 390 y a 1280: la landing dio 30 controles y **cero** bajo el
    piso, la ficha dio 9 y cero —el lightbox abierto incluido, sus tres botones
    miden 44—, y el listado dio uno: `.btn-clear`, **55x21**.

    `ui.md` pide 44x44 y lo justifica con «el panel se usa desde el celular en
    visitas». Este link vive en la barra de filtros, que es donde mas se toca.

    **El subrayado tiene que salir de `text-decoration`, no de `border-bottom`.**
    Con el borde, la caja de 44px deja la linea pegada al piso de la caja, a
    20px del texto: el control se agranda y de paso se rompe. Por eso el test
    mira las dos cosas.
    """

    def test_limpiar_llega_al_piso_tactil(self, listado: str):
        cuerpo = _regla(listado, ".btn-clear")
        m = re.search(r"min-height:\s*(\d+)px", cuerpo)
        assert m, "`.btn-clear` no declara min-height: vuelve a medir 21px de alto"
        assert int(m.group(1)) >= 44, (
            f"`.btn-clear` declara min-height {m.group(1)}px y el piso de "
            "`ui.md` es 44"
        )

    def test_el_subrayado_no_vuelve_al_borde(self, listado: str):
        cuerpo = _regla(listado, ".btn-clear")
        assert "border-bottom" not in cuerpo, (
            "`.btn-clear` volvio a subrayarse con `border-bottom`: adentro de "
            "una caja de 44px la linea se despega del texto 20px"
        )
        assert "text-decoration: underline" in cuerpo, (
            "`.btn-clear` se quedo sin subrayado — era lo unico que lo "
            "distinguia de un texto suelto"
        )

    def test_la_transicion_sigue_adentro_de_la_lista_de_ui_md(self, listado: str):
        """`ui.md` permite animar cinco propiedades. `text-decoration-color` no
        esta en la lista, y no hace falta: el subrayado hereda `currentColor`."""
        cuerpo = _regla(listado, ".btn-clear")
        m = re.search(r"transition:\s*([^;]+);", cuerpo)
        assert m, "`.btn-clear` perdio su transicion"
        permitidas = {"transform", "opacity", "color", "background-color", "border-color"}
        propiedades = {
            t.split()[0] for t in m.group(1).split(",") if t.strip()
        }
        fuera = propiedades - permitidas
        assert not fuera, f"`.btn-clear` anima {sorted(fuera)}, que no esta en ui.md"

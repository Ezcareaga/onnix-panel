"""El punto de la lista dice lo que realmente calcula.

Se llamaba `has_unread` y el `title` en pantalla decía «Mensajes nuevos». Las
dos cosas eran mentira: el flag no mira si alguien leyó algo, mira si el
**último mensaje es entrante** — o sea si está **sin responder**.

De ahí salía el "bug" de que no se apagaba al abrir la conversación. Con el
nombre correcto no es un bug: abrir no es contestar, y la pelota sigue del lado
del asesor hasta que escribe.

Un nombre que miente cuesta más que uno feo: alguien lo va a "arreglar" para
que se apague al abrir, y ahí sí se rompe.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PANEL = Path(__file__).resolve().parent.parent
_SERVICE = _PANEL / "app" / "services" / "conversation_service.py"
_LISTA = _PANEL / "app" / "templates" / "partials" / "conversation_list.html"
_CSS = _PANEL / "app" / "static" / "css" / "custom.css"

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_PY_DOCSTRING = re.compile(r'"""(?:.|\n)*?"""')


def _lista() -> str:
    return _HTML_COMMENT.sub("", _JINJA_COMMENT.sub("", _LISTA.read_text(encoding="utf-8")))


def _servicio() -> str:
    """Sin docstrings: el que explica el nombre viejo lo contiene."""
    return _PY_DOCSTRING.sub("", _SERVICE.read_text(encoding="utf-8"))


def _css() -> str:
    return _CSS_COMMENT.sub("", _CSS.read_text(encoding="utf-8"))


# ── el nombre ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "que,texto", [("el servicio", _servicio), ("la plantilla", _lista)]
)
def test_el_nombre_viejo_no_sobrevive(que, texto):
    assert "has_unread" not in texto(), f"{que} vuelve a llamarlo has_unread"


def test_el_servicio_expone_needs_reply():
    src = _servicio()
    assert 'item["needs_reply"]' in src
    assert "_enrich_with_needs_reply" in src


def test_la_plantilla_lee_la_clave_nueva():
    """Si el servicio renombra y la plantilla no, el punto desaparece sin que
    falle nada: Jinja resuelve un atributo inexistente como falso."""
    lista = _lista()
    assert "item.needs_reply" in lista
    assert lista.count("item.needs_reply") >= 3, (
        "quedó algún uso apuntando a la clave vieja"
    )


# ── lo que dice en pantalla ──────────────────────────────────────────────────

def test_el_titulo_dice_lo_que_el_flag_calcula():
    """Decía «Mensajes nuevos», que no es lo que mide."""
    lista = _lista()
    assert "Mensajes nuevos" not in lista, "volvió el título que miente"
    assert 'title="Sin responder"' in lista


def test_el_estado_tambien_esta_en_palabras():
    """`ui.md`: la señal visual no puede ser lo único. Un punto de color no lo
    lee un lector de pantalla y en el celular no hay `title`."""
    lista = _lista()
    bloque = lista.split("conv-dot", 1)[1][:400]
    assert "sr-only" in bloque and "Sin responder" in bloque


# ── el color ─────────────────────────────────────────────────────────────────

_HUES = (
    "indigo|purple|violet|blue|green|red|yellow|amber|orange|teal|cyan|pink|"
    "rose|emerald|lime|sky|fuchsia"
)


def test_el_punto_no_usa_un_matiz_de_la_paleta_por_defecto():
    """Era `bg-blue-500`, uno de los trece matices que contó el audit, para
    decir «esto exige acción», que el sistema de badges ya resuelve sin matiz.

    Se mira **el tag del punto**, no el archivo ni una ventana alrededor: en
    esa misma plantilla viven el icono y el chip de canal de WhatsApp y
    Telegram, que sí llevan el matiz de la marca del tercero. Si son o no una
    excepción a `ui.md` lo decide Ez; este test no se mete con eso, y por eso
    no puede ser un `not in` sobre el archivo entero — sería un test que falla
    por algo que no está juzgando.
    """
    lista = _lista()
    tag = re.search(r'<span class="conv-dot[^"]*"[^>]*>', lista)
    assert tag, "desapareció el punto"
    ofensores = re.findall(
        r"\b(?:bg|text|border)-(?:" + _HUES + r")-\d{2,3}\b", tag.group(0)
    )
    assert not ofensores, f"volvió el matiz al punto: {sorted(set(ofensores))}"


def test_el_punto_pasa_el_piso_de_un_grafico():
    """No es texto: el piso es 3:1 (WCAG 2.2 1.4.11, componentes gráficos)."""
    css = _css()
    m = re.search(r"\.conv-dot \{([^}]*)\}", css)
    assert m, "no existe .conv-dot"
    var = re.search(r"background:\s*var\(\s*--([a-z0-9-]+)\s*\)", m.group(1))
    assert var, ".conv-dot no sale de un token"

    root = css[css.index(":root"):css.index("}", css.index(":root"))]
    tokens = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", root))

    def srgb(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def lum(h):
        h = h.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b)

    fg, bg = lum(tokens[var.group(1)]), lum(tokens["surface"])
    ratio = (max(fg, bg) + 0.05) / (min(fg, bg) + 0.05)
    assert ratio >= 3.0, f"el punto da {ratio:.2f}:1 y el piso es 3:1"

"""El composer del hilo no se va con el scroll, y la altura no es un número.

Dos bugs en el mismo lugar:

1. `#panels-container` pedía su altura con `calc(100vh - 160px)`. El cromo real
   mide ~148px en escritorio y ~188px en el celular, así que en una sobraba y
   en la otra faltaba — y lo que quedaba abajo del fold era **el composer**,
   justo donde se escribe. Encima `100vh` en iOS Safari cuenta el alto sin la
   barra de direcciones, que después tapa contenido.

2. El composer vivía adentro de `#conv-thread`, que era el contenedor con
   scroll, así que se iba con los mensajes. Tenía un `mt-auto` para evitarlo,
   **inerte**: `#conv-thread` no era flex.

El arreglo es una cadena de flex sin ningún número: la pantalla no scrollea,
scrollea la lista de mensajes, y el composer es la última fila fija. Cada
eslabón necesita `min-height: 0` — un item de flex tiene `min-height: auto` y
se niega a achicarse por debajo de su contenido, así que sin eso el panel crece
hasta el largo del hilo y vuelve el bug.

Medido en Chrome a 390x844, 320x568 y 1280x800: el body no scrollea, el
composer queda visible en los tres, y la lista arranca al fondo.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PANEL = Path(__file__).resolve().parent.parent
_TEMPLATES = _PANEL / "app" / "templates"
_CONV = _TEMPLATES / "conversations.html"
_HILO = _TEMPLATES / "partials" / "conversation_thread.html"
_BASE = _TEMPLATES / "base.html"
_CSS = _PANEL / "app" / "static" / "css" / "custom.css"
_JS = _PANEL / "app" / "static" / "js" / "app.js"

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_JS_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _plantilla(path: Path) -> str:
    """Sin comentarios: el que explica un patrón prohibido lo contiene."""
    return _HTML_COMMENT.sub("", _JINJA_COMMENT.sub("", path.read_text(encoding="utf-8")))


def _js() -> str:
    return _JS_COMMENT.sub("", _JS.read_text(encoding="utf-8"))


def _css() -> str:
    return _CSS_COMMENT.sub("", _CSS.read_text(encoding="utf-8"))


# ── la altura no se calcula a mano ───────────────────────────────────────────

def test_el_panel_no_pide_su_altura_con_un_numero():
    """`calc(100vh - 160px)`: el 160 era inventado y el 100vh miente en iOS."""
    html = _plantilla(_CONV)
    assert "100vh" not in html, "volvió la altura calculada a mano"
    assert not re.search(r'style="[^"]*height:', html), (
        "volvió una altura por atributo style"
    )


def test_la_pantalla_de_conversaciones_se_marca_a_si_misma():
    """Sin la clase en el <body>, las reglas de layout no aplican a nada."""
    assert "{% block body_class %}page-conversations{% endblock %}" in _CONV.read_text(
        encoding="utf-8"
    )
    assert "{% block body_class %}" in _BASE.read_text(encoding="utf-8"), (
        "base.html perdió el hook de clase de body"
    )


# ── la cadena de flex ────────────────────────────────────────────────────────

# Cada eslabón entre el <body> y #panels-container. Si uno solo se olvida de
# `min-height: 0`, crece hasta su contenido y el composer vuelve abajo del fold.
_ESLABONES = ["shell-row", "shell-column", "main.main-content", "conv-shell"]


@pytest.mark.parametrize("eslabon", _ESLABONES)
def test_cada_eslabon_de_la_cadena_se_deja_achicar(eslabon):
    css = _css()
    bloques = re.findall(
        r"body\.page-conversations[^{]*\b" + re.escape(eslabon) + r"\b[^{]*\{([^}]*)\}",
        css,
    )
    assert bloques, f"ninguna regla de .page-conversations alcanza a {eslabon}"
    assert any("min-height: 0" in b for b in bloques), (
        f"{eslabon} no declara min-height: 0 y va a crecer hasta su contenido"
    )


@pytest.mark.parametrize("clase", ["shell-row", "shell-column"])
def test_el_shell_expone_sus_asideros(clase):
    """Las clases no pintan nada: existen para que una vista pueda pedir que la
    pantalla no scrollee. Si desaparecen, el CSS deja de alcanzar en silencio."""
    assert clase in _plantilla(_BASE), f"base.html perdió .{clase}"


def test_la_pantalla_usa_dvh_con_fallback():
    """`100dvh` es el alto útil de verdad; el `100vh` de arriba es el fallback."""
    css = _css()
    m = re.search(r"body\.page-conversations \.shell-row \{([^}]*)\}", css)
    assert m, "no existe la regla de altura del shell"
    cuerpo = m.group(1)
    assert "100dvh" in cuerpo, "sin dvh el bug vuelve en iOS Safari"
    assert cuerpo.index("100vh;") < cuerpo.index("100dvh"), (
        "el fallback tiene que ir ANTES, o pisa al dvh"
    )


# ── quién scrollea ───────────────────────────────────────────────────────────

def test_el_scroller_es_la_lista_y_no_el_panel():
    """Si `#conv-thread` scrollea, el composer se va con los mensajes."""
    conv, hilo = _plantilla(_CONV), _plantilla(_HILO)
    thread_div = re.search(r'<div id="conv-thread"[^>]*>', conv)
    assert thread_div, "desapareció #conv-thread"
    assert "overflow-y-auto" not in thread_div.group(0), (
        "#conv-thread volvió a ser el scroller: el composer se va con el scroll"
    )
    assert "flex flex-col" in thread_div.group(0), "#conv-thread no es la columna"

    lista = re.search(r'<div id="message-list"[^>]*', hilo)
    assert lista, "desapareció #message-list"
    assert "overflow-y-auto" in lista.group(0), "la lista de mensajes no scrollea"
    assert "min-h-0" in lista.group(0), (
        "sin min-h-0 la lista no se achica y empuja al composer fuera"
    )


def test_el_composer_es_una_fila_fija_y_no_depende_de_mt_auto():
    """El `mt-auto` era inerte porque el contenedor no era flex. Ahora sobra."""
    hilo = _plantilla(_HILO)
    assert "mt-auto" not in hilo, "volvió el mt-auto, que no hace nada acá"
    composer = re.search(r'<div class="[^"]*border-t bg-white p-3[^"]*"', hilo)
    assert composer, "desapareció el bloque del composer"
    assert "flex-shrink-0" in composer.group(0), (
        "el composer se puede achicar y va a desaparecer con hilos largos"
    )


def test_el_javascript_scrollea_la_lista_y_no_el_panel():
    """`app.js` scrolleaba `#conv-thread`. Si sigue haciéndolo, el autoscroll al
    llegar un mensaje deja de funcionar sin dar ningún error."""
    js = _js()
    assert "function threadScroller()" in js
    assert "getElementById('message-list')" in js
    scroll_part = js.split("GLOBAL ERROR TOAST")[0]
    assert "getElementById('conv-thread')" not in scroll_part, (
        "app.js sigue tratando a #conv-thread como el scroller"
    )
    # #conv-thread sigue siendo el TARGET de los swaps: eso no cambia.
    assert "target.id === 'conv-thread'" in scroll_part


def test_no_quedan_stickies_compensando_al_layout_viejo():
    """La cabecera y el aviso de 24h eran `sticky` porque el panel scrolleaba
    entero. Ahora son filas de la columna: un sticky ahí flota sobre nada."""
    hilo = _plantilla(_HILO)
    assert "sticky" not in hilo, "quedó un sticky del layout viejo"

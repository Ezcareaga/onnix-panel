"""El drawer de plantillas es un `<dialog>` nativo, y se cierra por Alpine.

Archivo aparte de `test_send_template_drawer.py`, que es de integración y
prueba las rutas (`/conversations/contacts/search`, `send_template_new`,
normalización de teléfonos) contra la base. Éste no toca la base: mira el
markup y el CSS. Dos cosas distintas, dos archivos.

Era un `<div x-show>` con backdrop propio y **cero focus trap**: con el drawer
abierto se podía tabular hasta los controles de atrás, y el Escape lo cerraba
solo porque había un `@keydown.escape.window` puesto a mano. Es el último de
los siete modales del panel que quedaba sin convertir.

La regla que fija este archivo, y que no es de estilo:

    quien cierra el drawer es SIEMPRE `drawerOpen = false`, nunca
    `$el.close()` a mano.

`x-effect` solo vuelve a correr cuando la variable **cambia**. Si un botón
cerrara el `<dialog>` por su cuenta, `drawerOpen` quedaría en `true` con el
drawer cerrado, y el próximo `drawerOpen = true` no sería un cambio: el drawer
no volvería a abrir nunca más. Lo encontré midiendo en Chrome — abría, cerraba
con la X, y ya no volvía a abrir.

`@close` queda para el único camino que no pasa por Alpine: la tecla Escape,
que maneja el navegador.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PANEL = Path(__file__).resolve().parent.parent
_DRAWER = _PANEL / "app" / "templates" / "partials" / "send_template_drawer.html"
_CSS = _PANEL / "app" / "static" / "css" / "custom.css"

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _drawer() -> str:
    """Sin comentarios: el que explica el patrón viejo lo contiene entero."""
    return _HTML_COMMENT.sub("", _JINJA_COMMENT.sub("", _DRAWER.read_text(encoding="utf-8")))


def _css() -> str:
    return _CSS_COMMENT.sub("", _CSS.read_text(encoding="utf-8"))


def _tag_dialog(html: str) -> str:
    m = re.search(r"<dialog[^>]*>", html, re.S)
    assert m, "el drawer dejó de ser un <dialog>"
    return m.group(0)


# ── es un dialog nativo ──────────────────────────────────────────────────────

def test_el_drawer_es_un_dialog_nativo():
    """El `<dialog>` da focus trap, Escape, ::backdrop y el resto de la página
    inertizada. Nada de eso estaba antes."""
    html = _drawer()
    assert "<dialog" in html
    assert 'x-teleport="body"' in html, "el drawer perdió el teleport"


def test_no_queda_backdrop_hecho_a_mano():
    """El `::backdrop` lo pinta el navegador. Un div de fondo propio vuelve a
    dejar el resto de la página tabulable."""
    html = _drawer()
    assert "fixed inset-0 bg-black" not in html, "volvió el backdrop a mano"


def test_no_queda_el_escape_a_mano():
    """El `<dialog>` cierra con Escape solo. Un `@keydown.escape.window` además
    dispara con el drawer cerrado, sobre cualquier tecla Escape de la página."""
    assert "keydown.escape" not in _drawer()


def test_el_dialog_se_anuncia_con_su_titulo():
    tag = _tag_dialog(_drawer())
    m = re.search(r'aria-labelledby="([^"]+)"', tag)
    assert m, "el <dialog> no dice qué es"
    assert f'id="{m.group(1)}"' in _drawer(), (
        f"aria-labelledby apunta a #{m.group(1)}, que no existe en el drawer"
    )


# ── la regla que importa ─────────────────────────────────────────────────────

def test_nadie_cierra_el_dialog_por_su_cuenta():
    """Si un control llama a `close()` directo, `drawerOpen` queda en true con
    el drawer cerrado y el próximo `drawerOpen = true` no es un cambio: no
    vuelve a abrir. Verificado en Chrome antes de escribir este test."""
    # El `x-effect` es el ÚNICO lugar que debe llamar close(): es el mecanismo,
    # y corre como consecuencia de que `drawerOpen` cambió. Se lo saca antes de
    # buscar, porque lo que este test prohíbe son los controles que se saltean
    # ese camino.
    html = re.sub(r'x-effect="[^"]*"', "", _drawer())
    ofensores = re.findall(r"\.close\(\)", html)
    assert not ofensores, (
        f"{len(ofensores)} control(es) cierran el <dialog> sin avisarle a "
        "Alpine. Tiene que ser `drawerOpen = false`."
    )


@pytest.mark.parametrize("atributo", ["@close", "@click.self"])
def test_el_dialog_conserva_sus_dos_salidas(atributo):
    """`@close` cubre el Escape, que no pasa por Alpine. `@click.self` cubre el
    toque en el fondo: en un `<dialog>` el ::backdrop no es un nodo, así que ese
    click llega al propio `<dialog>`."""
    assert atributo in _tag_dialog(_drawer()), f"el drawer perdió {atributo}"


def test_el_close_resetea_el_paso():
    """Si no, al reabrir aparece en el paso 2 del flujo anterior."""
    tag = _tag_dialog(_drawer())
    m = re.search(r'@close="([^"]*)"', tag)
    assert m, "no hay handler de close"
    assert "drawerOpen = false" in m.group(1)
    assert "drawerStep = 'select'" in m.group(1)
    assert "resetDrawer()" in m.group(1)


def test_el_x_effect_solo_abre_y_cierra():
    """A diferencia de los modales de visita, acá NO va `htmx.process`: los dos
    formularios son `method="POST"` planos y el buscador de contactos llama a
    `htmx.ajax()` a mano, que no necesita el nodo indexado."""
    tag = _tag_dialog(_drawer())
    m = re.search(r'x-effect="([^"]*)"', tag)
    assert m, "el drawer perdió el x-effect"
    assert "showModal()" in m.group(1) and "close()" in m.group(1)
    assert "htmx.process" not in m.group(1), (
        "htmx.process acá no hace nada: es copiar el patrón del otro modal sin "
        "mirar qué hace"
    )
    assert not re.search(r'<form[^>]*\shx-post', _drawer()), (
        "si el drawer suma un form htmx, htmx.process vuelve a hacer falta"
    )


# ── el CSS ───────────────────────────────────────────────────────────────────

def test_la_variante_drawer_existe_y_va_pegada_a_la_derecha():
    css = _css()
    m = re.search(r"\.drawer:modal \{([^}]*)\}", css)
    assert m, "falta .drawer:modal — sin eso `dialog:modal { margin: auto }` lo centra"
    assert "margin: 0 0 0 auto" in m.group(1)


def test_el_drawer_usa_dvh():
    """`100vh` en iOS Safari cuenta el alto sin la barra de direcciones, y el
    pie del drawer queda tapado."""
    m = re.search(r"^\.drawer \{([^}]*)\}", _css(), re.M)
    assert m, "falta la regla .drawer"
    assert "100dvh" in m.group(1)
    assert "100vh" not in m.group(1).replace("100dvh", ""), (
        "el drawer no necesita fallback: si dvh no existe, tampoco existe el "
        "problema que resuelve"
    )


def test_el_drawer_tiene_su_backdrop():
    assert re.search(r"\.drawer::backdrop \{[^}]*background", _css()), (
        "sin ::backdrop el fondo queda transparente y no se ve que hay un modal"
    )

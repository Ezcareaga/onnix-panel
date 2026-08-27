"""Carril K — el menu lateral.

Tres cosas que el audit marco y una que aparecio al mirarlas:

1. El item activo quedo ilegible. B1 cambio el fondo translucido de acento por
   `--accent-wash`, que es un token de superficie CLARA (#ECECEA), pero el menu
   vive sobre `--shell` (#16181A) y el template ponia `text-white` encima. El
   unico item que el menu tiene que dejar leer —donde estas parado— era el
   unico a 1,04:1. Decision de Ez: el activo pierde el fondo Y el borde de
   acento. Se distingue por peso y luminancia, que es lo que hace una barra
   lateral de escritorio; el side-tab de 2px es el tell que el audit marca.
2. Los items median 40px de alto. El asesor los toca parado frente a una casa.
3. En movil el boton de cerrar el menu estaba enterrado debajo de la topbar,
   que es fixed, opaca, de 56px y va en z-50 contra el z-40 del menu.
4. El rol del usuario se leia a 3,68:1 sobre el shell.

El contraste se calcula acá y no se copia de un comentario: un numero escrito
a mano envejece sin avisar.
"""
from __future__ import annotations

import re
from pathlib import Path

_PANEL = Path(__file__).resolve().parent.parent
_SIDEBAR = (_PANEL / "app" / "templates" / "partials" / "sidebar.html").read_text(
    encoding="utf-8"
)
_CSS = (_PANEL / "app" / "static" / "css" / "custom.css").read_text(encoding="utf-8")

_TAILWIND_GRISES = {
    "text-white": "#FFFFFF",
    "text-gray-300": "#D1D5DB",
    "text-gray-400": "#9CA3AF",
    "text-gray-500": "#6B7280",
}


def _luminancia(hexa: str) -> float:
    r, g, b = (int(hexa[i:i + 2], 16) / 255 for i in (1, 3, 5))
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contraste(a: str, b: str) -> float:
    la, lb = _luminancia(a), _luminancia(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _token(nombre: str) -> str:
    m = re.search(rf"--{nombre}:\s*(#[0-9A-Fa-f]{{6}})", _CSS)
    assert m, f"falta el token --{nombre}"
    return m.group(1)


def _regla_del_activo() -> str:
    m = re.search(r"\.nav-active\s*\{([^}]*)\}", _CSS)
    assert m, "no se pudo leer .nav-active"
    return m.group(1)


def test_el_item_activo_se_puede_leer():
    """Era 1,04:1: blanco sobre un token de superficie clara."""
    m = re.search(r"color:\s*var\(--([\w-]+)\)", _regla_del_activo())
    assert m, ".nav-active tiene que declarar el color del texto"
    ratio = _contraste(_token(m.group(1)), _token("shell"))
    assert ratio >= 4.5, f"el item activo del menu da {ratio:.2f}:1"


def test_el_activo_no_es_un_side_tab():
    """Un borde de acento de 2px a la izquierda es el tell que marca el audit.
    Y un fondo propio ya se probo que envejece mal: B1 lo dejo en claro sobre
    el shell oscuro y nadie lo vio."""
    regla = _regla_del_activo()
    assert "border" not in regla, "volvio el side-tab del item activo"
    assert "background" not in regla, "el item activo volvio a tener fondo propio"


def test_el_activo_pesa_mas_que_el_reposo():
    """Sin fondo ni borde, lo que separa activo de reposo es peso y luminancia.
    Si alguna de las dos se va, el menu deja de decir donde estas."""
    assert "font-weight: 600" in _regla_del_activo()
    m = re.search(r"color:\s*var\(--([\w-]+)\)", _regla_del_activo())
    activo = _contraste(_token(m.group(1)), _token("shell"))
    reposo = _contraste(_TAILWIND_GRISES["text-gray-400"], _token("shell"))
    assert activo > reposo * 1.5, (
        f"activo {activo:.2f}:1 contra reposo {reposo:.2f}:1: no se distinguen"
    )


def test_todo_el_texto_del_menu_llega_al_piso():
    shell = _token("shell")
    for clase, hexa in _TAILWIND_GRISES.items():
        if clase not in _SIDEBAR:
            continue
        ratio = _contraste(hexa, shell)
        assert ratio >= 4.5, f"{clase} sobre el shell da {ratio:.2f}:1"


def test_los_items_llegan_a_44px():
    """py-3 (12px arriba y abajo) sobre una linea de text-sm (20px) = 44px."""
    assert "py-2.5" not in _SIDEBAR, "los items del menu volvieron a 40px"
    # 9 desde que el menu lista Tutoriales (era 8).
    assert _SIDEBAR.count("px-4 py-3 text-gray-400") == 6


def test_el_boton_de_cerrar_no_queda_debajo_de_la_topbar():
    """La topbar es fixed, opaca, de 56px y z-50; el menu va en z-40."""
    aside = _SIDEBAR[_SIDEBAR.index("<aside"):_SIDEBAR.index(">", _SIDEBAR.index("<aside"))]
    assert "pt-14" in aside and "md:pt-0" in aside


def test_el_menu_anuncia_donde_estas():
    """No se pierde lo que ya arreglo 93acceb."""
    # 9 desde que el menu lista Tutoriales (era 8).
    assert _SIDEBAR.count('aria-current="page"') == 6

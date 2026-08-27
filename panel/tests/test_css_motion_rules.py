"""Carril B2 — las reglas de animacion de `.claude/rules/ui.md`, verificables.

El detector marcaba `width` en dos barras, diez duraciones fuera de rango y la
ausencia total de un bloque `prefers-reduced-motion`. Nada de eso se ve mirando
la pantalla: hay que medirlo. Este archivo lo mide.

ui.md:98-108 — 150-200ms, `ease-out`, y solo `transform`, `opacity`, `color` y
`background-color`. Nunca `width`, `height`, `top`, `left`, `right`, `bottom`,
`margin`, `padding` ni `font-weight`.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_CSS_DIR = Path(__file__).resolve().parent.parent / "app" / "static" / "css"
_CUSTOM = _CSS_DIR / "custom.css"

_REDUCED = re.compile(
    r"@media\s*\(\s*prefers-reduced-motion:\s*reduce\s*\)\s*\{", re.IGNORECASE
)
_PROHIBIDAS = (
    "width", "height", "top", "left", "right", "bottom",
    "margin", "padding", "font-weight",
)


def _sin_bloque_reduced(css: str) -> str:
    """El CSS sin el bloque de reduced-motion: ahi los 0.01ms son correctos."""
    m = _REDUCED.search(css)
    if not m:
        return css
    i, prof = m.end(), 1
    while i < len(css) and prof:
        prof += (css[i] == "{") - (css[i] == "}")
        i += 1
    return css[: m.start()] + css[i:]


def test_existe_un_solo_bloque_global_de_reduced_motion():
    css = _CUSTOM.read_text(encoding="utf-8")
    assert len(_REDUCED.findall(css)) == 1, (
        "ui.md:107 lo pide en un solo bloque global; varios se desincronizan"
    )


def test_el_bloque_de_reduced_motion_va_al_final():
    """Gana por cascada sin necesitar !important en cada regla."""
    css = _CUSTOM.read_text(encoding="utf-8")
    m = _REDUCED.search(css)
    assert m is not None
    cola = css[m.start():]
    assert "@media" not in cola[len("@media"):], (
        "hay reglas despues del bloque de reduced-motion: van a pisarlo"
    )


@pytest.mark.parametrize("prop", _PROHIBIDAS)
def test_ninguna_transicion_anima_layout(prop):
    css = _sin_bloque_reduced(_CUSTOM.read_text(encoding="utf-8"))
    for linea in css.splitlines():
        limpia = linea.split("/*")[0].strip()
        if not limpia.startswith(("transition:", "transition-property:")):
            continue
        propiedades = re.split(r"[:,]", limpia)[1:]
        for trozo in propiedades:
            nombre = trozo.strip().split()[0] if trozo.strip() else ""
            assert nombre != prop, (
                f"{limpia!r} anima {prop!r}, que es layout (ui.md:101)"
            )


def test_las_duraciones_estan_entre_150_y_200ms():
    """Unica excepcion: el spinner de HTMX, que es un indicador continuo."""
    css = _sin_bloque_reduced(_CUSTOM.read_text(encoding="utf-8"))
    fuera = []
    for linea in css.splitlines():
        if "htmxSpin" in linea:
            continue
        for valor, unidad in re.findall(r"(\d*\.?\d+)(ms|s)\b", linea):
            ms = float(valor) * (1 if unidad == "ms" else 1000)
            if not 150 <= ms <= 200:
                fuera.append((linea.strip(), ms))
    assert not fuera, f"duraciones fuera de 150-200ms: {fuera}"


def test_no_quedan_ease_in():
    css = _CUSTOM.read_text(encoding="utf-8")
    malos = [l.strip() for l in css.splitlines() if re.search(r"\bease-in(-out)?\b", l)]
    assert not malos, f"ui.md pide ease-out: {malos}"


@pytest.mark.parametrize(
    "clase",
    ["card-hover", "animate-fadeIn", "animate-slideUp",
     "stagger-1", "stagger-2", "stagger-3", "stagger-4", "stagger-5"],
)
def test_las_clases_borradas_no_vuelven(clase):
    """Vivian adentro de los fragmentos que HTMX reemplaza, asi que el
    dashboard, /stats y bot_health se desvanecian enteros en cada refresco."""
    for path in sorted((Path(__file__).resolve().parent.parent / "app" / "templates").rglob("*.html")):
        for attr in re.findall(r'class="([^"]*)"', path.read_text(encoding="utf-8")):
            assert clase not in attr.split(), f"{path.name} volvio a usar {clase}"

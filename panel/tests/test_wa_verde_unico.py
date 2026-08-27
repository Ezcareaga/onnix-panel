"""El verde de WhatsApp es uno solo, y lo dicen los dos archivos que lo pintan.

La misma acción —abrir WhatsApp— vive en dos superficies públicas del mismo
sitio: la píldora flotante de la landing y el CTA de la ficha del portal. Hasta
el 2026-08-23 la landing la pintaba de acento y la ficha de verde. No era una
decisión: es que se escribieron en momentos distintos, y ninguna de las dos
sabía de la otra.

Ez decidió verde en las dos. Este archivo es lo que impide la cuarta
divergencia: los tres casos que ya ocurrieron —una tinta, un lavado de acento, un
color de borde— fueron todos de token, y todos se arreglaron a mano después de
que alguien los viera.

**Ningún número está escrito a mano acá.** Los hex salen de los dos archivos y
el contraste se calcula. Blanco sobre este verde da 1,75:1, que fue el fallo de
AA que la ficha arregló en agosto: si alguien vuelve a poner la tinta clara,
esto se pone rojo antes de que llegue a producción.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[2]
_LANDING = _RAIZ / "landing" / "assets" / "css" / "styles.css"
_FICHA = _RAIZ / "panel" / "app" / "templates" / "public" / "property.html"

AA_TEXTO_NORMAL = 4.5  # WCAG 2.1 SC 1.4.3. Es la norma, no una medicion nuestra.


def _token(fuente: str, nombre: str) -> str:
    """El valor de un `--token: #hex;` del `:root`, en mayusculas."""
    m = re.search(rf"--{re.escape(nombre)}:\s*(#[0-9A-Fa-f]{{3,8}})\s*;", fuente)
    assert m, f"falta --{nombre}"
    return m.group(1).upper()


def _rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _luminancia(rgb: tuple[int, int, int]) -> float:
    def lineal(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lineal(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contraste(a: str, b: str) -> float:
    la, lb = _luminancia(_rgb(a)), _luminancia(_rgb(b))
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def test_la_formula_da_los_valores_conocidos():
    """Sin esto una formula rota deja pasar cualquier combinacion."""
    assert _contraste("#FFFFFF", "#000000") == pytest.approx(21.0, abs=0.01)
    assert _contraste("#FFFFFF", "#FFFFFF") == pytest.approx(1.0, abs=0.001)


@pytest.mark.parametrize("nombre", ["wa-green", "wa-green-hv"])
def test_las_dos_superficies_declaran_el_mismo_verde(nombre):
    landing = _token(_LANDING.read_text(encoding="utf-8"), nombre)
    ficha = _token(_FICHA.read_text(encoding="utf-8"), nombre)
    assert landing == ficha, (
        f"--{nombre} vale {landing} en la landing y {ficha} en la ficha: "
        "la misma accion con dos tintas, otra vez"
    )


def test_la_pildora_de_la_landing_usa_el_token_y_no_el_oro():
    css = _LANDING.read_text(encoding="utf-8")
    bloque = css[css.index(".wa-flotante {"):css.index(".wa-flotante svg")]
    assert "var(--wa-green)" in bloque, "la pildora volvio a pintarse sin el token"
    assert "var(--accent)" not in bloque and "var(--accent-light)" not in bloque, (
        "la pildora volvio al acento: la ficha quedaria en verde y la landing no"
    )


@pytest.mark.parametrize("fondo", ["wa-green", "wa-green-hv"])
def test_la_tinta_de_la_pildora_pasa_AA(fondo):
    """El texto va en --black. En blanco da 1,75:1 y fue un fallo real."""
    css = _LANDING.read_text(encoding="utf-8")
    ratio = _contraste(_token(css, fondo), _token(css, "black"))
    assert ratio >= AA_TEXTO_NORMAL, f"--black sobre --{fondo} da {ratio:.2f}:1"

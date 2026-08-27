"""Los grises de Tailwind que no llegan al piso de contraste.

Medido con la formula de luminancia relativa de WCAG 2.1 sobre los valores
reales de la paleta de Tailwind:

    clase           sobre #FFFFFF   sobre --paper   usos que habia
    text-gray-300        1,47            1,37             63
    text-gray-400        2,54            2,37            239

Contra un piso de 4,5:1 para texto y 3,0:1 para iconos y bordes. Eran 288 en
superficie clara: 86 <p>, 52 <span>, 20 <dt>, 8 <label> — texto que alguien
tiene que leer para trabajar, no decoracion.

Ahora salen de la escala de tinta del :root:
    text-onnix-ink-400      5,00:1   texto secundario
    text-onnix-rule-strong  3,15:1   iconos decorativos y deshabilitados

Los 14 usos del shell oscuro se quedan como estan: ahi el mismo gris da
12,08:1 y 7,01:1.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

# El shell oscuro. Son las dos unicas superficies del panel con fondo #16181A.
_SUPERFICIES_OSCURAS = {"sidebar.html", "login.html"}

_FLOJOS = re.compile(
    r"(?<![-\w])(?:hover:|group-hover:|focus:|active:)?text-gray-(300|400)(?![-\w])"
)


def _plantillas_claras() -> list[Path]:
    return sorted(
        p for p in _TEMPLATES.rglob("*.html")
        if "public/" not in str(p) and p.name not in _SUPERFICIES_OSCURAS
    )


def test_hay_plantillas_para_chequear():
    assert len(_plantillas_claras()) >= 40


@pytest.mark.parametrize("path", _plantillas_claras(), ids=lambda p: p.name)
def test_sin_grises_por_debajo_del_piso(path):
    lineas = [
        f"  :{n}  {l.strip()[:88]}"
        for n, l in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _FLOJOS.search(l)
    ]
    assert not lineas, (
        f"{path.name} usa text-gray-300 (1,47:1) o text-gray-400 (2,54:1) sobre "
        f"fondo claro. Va text-onnix-ink-400 (5,00:1), o text-onnix-rule-strong "
        f"(3,15:1) si es un icono decorativo o un estado deshabilitado:\n"
        + "\n".join(lineas)
    )


def test_el_shell_oscuro_sigue_pudiendo_usarlos():
    """No es una excepcion olvidada: ahi el mismo gris da 12,08:1 y 7,01:1."""
    usos = sum(
        len(_FLOJOS.findall((_TEMPLATES / rel).read_text(encoding="utf-8")))
        for rel in ("partials/sidebar.html", "login.html")
    )
    assert usos > 0, (
        "el shell oscuro dejo de usar los grises claros; si fue a proposito, "
        "borrar esta excepcion en vez de dejarla mintiendo"
    )

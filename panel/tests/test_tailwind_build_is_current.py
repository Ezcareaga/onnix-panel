"""El CSS compilado tiene que contener las clases arbitrarias de los templates.

tailwind.css es un artefacto generado y se commitea a mano. Cuando alguien
agrega una clase de valor arbitrario (`text-[0.65rem]`, `min-w-[90px]`) y no
recompila, la clase no existe y el elemento se renderiza al tamano heredado,
sin error ni aviso. Paso con 43 etiquetas a la vez, y el `?v=` a mano de
base.html lo escondia todavia mas.

Solo mira valores arbitrarios: son los unicos que no se pueden confundir con
una clase generada por otra ruta, y son los que desaparecen en silencio.

Recompilar:
    npx tailwindcss@3.4.19 -i app/static/css/input.css \
        -o app/static/css/tailwind.css --minify
y subir el `?v=` de base.html.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PANEL = Path(__file__).resolve().parent.parent
_TEMPLATES = _PANEL / "app" / "templates"
_CSS = _PANEL / "app" / "static" / "css" / "tailwind.css"

_CLASS_ATTR = re.compile(r'\bclass="([^"]*)"')
# `text-[0.65rem]`, `md:min-w-[90px]`, `bg-[#FAF7EE]` — nada de Jinja adentro.
_ARBITRARIA = re.compile(r"^[a-z0-9:./-]*\[[^\]]+\]$")
# Tailwind escapa con backslash todo lo que no es alfanumerico ni guion...
_A_ESCAPAR = re.compile(r"([^a-zA-Z0-9_-])")
# ...salvo la coma, que sale como escape unicode de CSS: `\2c ` con el espacio
# final que lo termina. Buscarla como `\,` daba "no esta compilada" sobre una
# clase que SI estaba —`transition-[background-color,transform]`—, o sea el
# test mandaba a recompilar en loop. Es un falso rojo, que es la otra cara del
# verde que no prueba nada.
_ESCAPES_CSS = {",": "\\2c "}


def _clases_arbitrarias() -> list[str]:
    encontradas: set[str] = set()
    for path in sorted(_TEMPLATES.rglob("*.html")):
        for attr in _CLASS_ATTR.findall(path.read_text(encoding="utf-8")):
            for cls in attr.split():
                if "{" in cls or "}" in cls:
                    continue  # interpolacion de Jinja, no una clase literal
                if _ARBITRARIA.match(cls):
                    encontradas.add(cls)
    return sorted(encontradas)


def test_hay_clases_arbitrarias_para_chequear():
    """Si el scanner deja de encontrar clases, el test de abajo no protege nada."""
    assert len(_clases_arbitrarias()) > 20


def _selector(cls: str) -> str:
    """El selector tal cual lo emite Tailwind para una clase arbitraria."""
    def esc(m: re.Match) -> str:
        ch = m.group(1)
        return _ESCAPES_CSS.get(ch, "\\" + ch)
    return "." + _A_ESCAPAR.sub(esc, cls)


def test_el_selector_traduce_la_coma_como_tailwind():
    """La coma sale como `\\2c ` y no como `\\,`: sin esto el test es un falso rojo."""
    assert _selector("transition-[background-color,transform]") == (
        r".transition-\[background-color\2c transform\]"
    )
    assert _selector("text-[0.65rem]") == r".text-\[0\.65rem\]"


@pytest.mark.parametrize("cls", _clases_arbitrarias())
def test_la_clase_arbitraria_esta_compilada(cls):
    selector = _selector(cls)
    assert selector in _CSS.read_text(encoding="utf-8"), (
        f"{cls!r} se usa en un template pero no esta en tailwind.css: "
        "el elemento se renderiza al valor heredado. Recompilar (ver el "
        "docstring de este archivo) y subir el ?v= de base.html."
    )

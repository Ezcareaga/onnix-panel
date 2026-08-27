"""Carril B4 — un solo <h1> por vista, y un solo lugar para la accion primaria.

Antes: base.html pintaba el nombre de la pantalla en un <h2> de la cabecera y
cada vista lo repetia en su propio <h1>. En 12 de 14 vistas el nombre aparecia
dos veces, y el <h2> venia ANTES que el <h1> en el DOM, asi que un lector de
pantalla anunciaba el nivel 2 antes del 1.

Ahora base.html renderiza `.page-head` dentro de <main> con el <h1> y un
`{% block page_actions %}`. Ese bloque es el unico lugar de la vista que tiene
espacio para una accion primaria, que es como «una sola accion primaria por
vista» deja de ser una regla que alguien recuerda.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
_BASE = _TEMPLATES / "base.html"

# Las que extienden base.html: son las que heredan el .page-head.
_HIJAS = sorted(
    p for p in _TEMPLATES.rglob("*.html")
    if 'extends "base.html"' in p.read_text(encoding="utf-8")
)

_H1 = re.compile(r"<h1\b", re.IGNORECASE)
_BLOQUE_TITULO = re.compile(r"\{%\s*block page_title\s*%\}(.*?)\{%\s*endblock\s*%\}", re.DOTALL)
_COMENTARIO = re.compile(r"\{#.*?#\}", re.DOTALL)


def _sin_comentarios(path: Path) -> str:
    """Los comentarios Jinja nombran <h1> al explicar por que ya no hay uno."""
    return _COMENTARIO.sub("", path.read_text(encoding="utf-8"))


def test_hay_vistas_para_chequear():
    # Eran 10; se fueron las dos de propiedades y la de salud del bot.
    assert len(_HIJAS) >= 7, f"solo {len(_HIJAS)} vistas extienden base.html"


def test_base_pinta_un_solo_h1():
    assert len(_H1.findall(_sin_comentarios(_BASE))) == 1


@pytest.mark.parametrize("path", _HIJAS, ids=lambda p: p.name)
def test_la_vista_no_repite_el_h1(path):
    """El <h1> lo pone base.html. Uno propio son dos niveles 1 en la pagina."""
    encontrados = _H1.findall(_sin_comentarios(path))
    assert not encontrados, (
        f"{path.name} declara su propio <h1>; el titulo va en page_title y lo "
        "pinta base.html"
    )


@pytest.mark.parametrize("path", _HIJAS, ids=lambda p: p.name)
def test_page_title_es_texto_plano(path):
    """Si lleva markup, el <h1> hereda el tamano del <span> de adentro.

    Es lo que pasaba: un <h2> declarado text-lg font-semibold se renderizaba
    en 14px gris porque el bloque traia <span class="text-sm text-gray-500">.
    Ademas el <title> se deriva de este bloque, y ahi el markup se ve crudo.
    """
    m = _BLOQUE_TITULO.search(_sin_comentarios(path))
    assert m, f"{path.name} no define page_title"
    cuerpo = m.group(1)
    assert "<" not in cuerpo, (
        f"{path.name} mete markup en page_title: {cuerpo.strip()[:70]!r}. "
        "Solo texto, con {{ }} y {% if %} si hace falta."
    )


def test_el_titulo_del_documento_sale_de_page_title():
    """El menu decia «Stats» y la pantalla «Estadisticas»: dos strings sueltos."""
    assert "self.page_title()" in _BASE.read_text(encoding="utf-8")

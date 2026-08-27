"""Ninguna tabla obliga a scrollear de costado en el celular. M4 del carril M.

Cuatro tablas quedaron con `min-w-[500..860px]` sin condición: abajo de 768px
son scroll horizontal, en las pantallas que el asesor abre parado frente a una
casa. `auth_audit_table` pedía 860px sobre un viewport de 390.

**G y J ya habían resuelto el patrón, y de dos formas distintas.** J renderiza
la tabla Y un set de cards aparte —dos markups, y en este repo todo lo escrito
dos veces ya divergió: la card de leads había perdido 7 de las 13 capacidades
de la fila—. G reacomoda la MISMA tabla con CSS. Gana G, y `.tabla-card` es su
versión genérica: en vez de un `grid-template-areas` por pantalla, cada celda
dice su encabezado con `data-label`.

La regla que fija este test: **si una tabla pide ancho mínimo, ese ancho es
condicional a `md:`** —o vive adentro de un contenedor que no se renderiza en
móvil—. Un `min-w-[Npx]` pelado es scroll horizontal garantizado.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

_COMENT_JINJA = re.compile(r"\{#.*?#\}", re.S)
_COMENT_HTML = re.compile(r"<!--.*?-->", re.S)
_TABLA = re.compile(r"<table\b[^>]*>", re.I | re.S)
_CLASES = re.compile(r'class="([^"]*)"')
# `min-w-[700px]` sin prefijo de breakpoint. `md:min-w-[700px]` no matchea.
_ANCHO_PELADO = re.compile(r"(?<![\w:-])min-w-\[[^\]]+\]")

# El portal público tiene sus 1.900 líneas de CSS autocontenido y su propia
# decisión de arquitectura abierta (la 2ª de las cuatro de J): no es de M4.
_FUERA_DE_ALCANCE = {"public"}


def _plantillas() -> list[Path]:
    return sorted(
        p for p in _TEMPLATES.rglob("*.html")
        if not (set(p.relative_to(_TEMPLATES).parts) & _FUERA_DE_ALCANCE)
    )


def _sin_comentarios(path: Path) -> str:
    return _COMENT_HTML.sub("", _COMENT_JINJA.sub("", path.read_text(encoding="utf-8")))


def _envuelta_en_solo_escritorio(src: str, pos: int) -> bool:
    """¿La tabla vive adentro de un `hidden md:block`?

    Se mira el `<div>` abierto más cercano antes de la tabla: es como lo
    resolvió `properties_table.html`, que no se renderiza abajo de 768px y en
    su lugar pone cards.
    """
    antes = src[:pos]
    for m in reversed(list(re.finditer(r"<div\b[^>]*>", antes, re.S))):
        clases = _CLASES.search(m.group(0))
        if clases and "hidden" in clases.group(1) and "md:block" in clases.group(1):
            return True
        break
    return False


def test_hay_tablas_para_revisar():
    total = sum(len(_TABLA.findall(_sin_comentarios(p))) for p in _plantillas())
    assert total >= 5, f"solo {total} tablas: el escaneo se rompió"


@pytest.mark.parametrize("path", _plantillas(), ids=lambda p: p.name)
def test_ninguna_tabla_pide_ancho_minimo_en_el_celular(path):
    src = _sin_comentarios(path)
    malas = []
    for m in _TABLA.finditer(src):
        clases = _CLASES.search(m.group(0))
        if not clases:
            continue
        pelados = _ANCHO_PELADO.findall(clases.group(1))
        if pelados and not _envuelta_en_solo_escritorio(src, m.start()):
            linea = src[: m.start()].count("\n") + 1
            malas.append(f"  :{linea}  {' '.join(pelados)}")
    assert not malas, (
        f"{path.name}: la tabla pide ancho mínimo sin condición, así que a "
        f"390px se scrollea de costado. Va `md:min-w-[…]` más la clase "
        f"`.tabla-card`, o un contenedor `hidden md:block`:\n" + "\n".join(malas)
    )


def test_el_detector_ve_un_ancho_pelado(tmp_path):
    """Mutación adentro del test: un detector roto pasa sin mirar nada."""
    p = tmp_path / "x.html"
    p.write_text('<table class="w-full min-w-[700px]">', encoding="utf-8")
    with pytest.raises(AssertionError):
        test_ninguna_tabla_pide_ancho_minimo_en_el_celular(p)

    p.write_text('<table class="w-full md:min-w-[700px]">', encoding="utf-8")
    test_ninguna_tabla_pide_ancho_minimo_en_el_celular(p)

    p.write_text('<div class="hidden md:block"><table class="w-full min-w-[700px]">',
                 encoding="utf-8")
    test_ninguna_tabla_pide_ancho_minimo_en_el_celular(p)

    p.write_text('{# antes decía min-w-[700px] #}<table class="w-full">', encoding="utf-8")
    test_ninguna_tabla_pide_ancho_minimo_en_el_celular(p)


# (plantilla que abre la tabla, plantilla que escribe las filas). No siempre es
# la misma: `settings.html` abre la tabla de usuarios y las filas salen de
# `user_row.html`, incluido desde `users_table.html`.
_TABLAS_REACOMODADAS = [
    ("contacts.html", "contacts.html"),
    ("settings.html", "partials/user_row.html"),
    ("partials/auth_audit_table.html", "partials/auth_audit_table.html"),
    ("partials/settings_form.html", "partials/settings_form.html"),
]


@pytest.mark.parametrize("tabla,filas", _TABLAS_REACOMODADAS, ids=lambda v: v)
def test_la_tabla_reacomodada_dice_el_nombre_de_cada_dato(tabla, filas):
    """Sin `thead` en móvil, un valor suelto no se sabe de qué columna es."""
    assert "tabla-card" in _sin_comentarios(_TEMPLATES / tabla), (
        f"{tabla}: la tabla no usa el patrón"
    )
    src_filas = _sin_comentarios(_TEMPLATES / filas)
    celdas = re.findall(r"<td\b[^>]*>", src_filas, re.S)
    assert celdas, f"{filas}: no se encontró ninguna celda"
    con_label = [c for c in celdas if "data-label" in c]
    assert con_label, f"{filas}: ninguna celda dice su encabezado"
    # Las celdas sin `data-label` son las de acciones y las de checkbox, que no
    # son un dato con nombre. Que existan está bien; que sean TODAS, no.
    sin_label = len(celdas) - len(con_label)
    assert sin_label <= 3, (
        f"{filas}: {sin_label} celdas sin `data-label`, de {len(celdas)}"
    )

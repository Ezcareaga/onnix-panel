"""Todo campo de formulario tiene un nombre accesible. M1 del carril M.

Eran **56 campos** `<input>`, `<select>` y `<textarea>` sin `<label>` ni
`aria-label`, en 12 plantillas, con el `placeholder` haciendo de etiqueta — y en
los `<select>` no hay ni placeholder: el filtro de estado de `/contacts` era un
combo sin nombre. Viola WCAG 1.3.1, 3.3.2 y 4.1.2, y el `placeholder` además
desaparece al tipear.

Es la única categoría entera ausente del roadmap original: `aria-label` no
aparece ni una vez en sus 381 líneas. No fue la omisión de un carril, nunca
entró.

**Este test es el barrido**, no el arreglo: sin él la categoría vuelve en el
próximo formulario, igual que volvieron los 105 controles chicos después de que
el carril B definiera `--tap: 44px`.

Dos cosas que el escaneo hace y son la diferencia entre medir y adivinar:

1. **Va por tag, no por línea.** El audit del 20/08 reportó tres `<img>` sin
   `alt` en la ficha y los tres lo tenían en la línea siguiente.
2. **Filtra comentarios de Jinja y de HTML primero.** Es la trampa propia de
   este repo: el comentario que explica un patrón prohibido lo contiene.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

_COMENT_JINJA = re.compile(r"\{#.*?#\}", re.S)
_COMENT_HTML = re.compile(r"<!--.*?-->", re.S)
_CAMPO = re.compile(r"<(input|select|textarea)\b[^>]*>", re.I | re.S)
_ATTR = re.compile(r'([a-zA-Z_:@.\-\[\]]+)\s*=\s*"([^"]*)"')
_LABEL_FOR = re.compile(r'<label\b[^>]*\bfor\s*=\s*"([^"]+)"', re.I | re.S)
_LABEL_ENVOLVENTE = re.compile(r"<label\b(?:(?!</label>).)*?</label>", re.I | re.S)

# Estos no llevan nombre accesible porque no reciben foco ni valor del usuario.
_SIN_NOMBRE = {"hidden", "submit", "button", "reset", "image"}


def _plantillas() -> list[Path]:
    return sorted(_TEMPLATES.rglob("*.html"))


def _campos_sin_nombre(path: Path) -> list[str]:
    """Devuelve una línea por campo sin nombre accesible."""
    src = _COMENT_HTML.sub("", _COMENT_JINJA.sub("", path.read_text(encoding="utf-8")))
    fors = set(_LABEL_FOR.findall(src))
    envueltos = "".join(_LABEL_ENVOLVENTE.findall(src))

    faltantes = []
    for m in _CAMPO.finditer(src):
        tag = m.group(0)
        attrs = dict(_ATTR.findall(tag))
        if (attrs.get("type") or "").lower() in _SIN_NOMBRE:
            continue
        if attrs.get("aria-label") or attrs.get("aria-labelledby"):
            continue
        if attrs.get("id") and attrs["id"] in fors:
            continue
        if tag in envueltos:
            continue
        linea = src[: m.start()].count("\n") + 1
        nombre = attrs.get("name") or attrs.get("id") or attrs.get("x-model") or "?"
        faltantes.append(f"  :{linea}  <{m.group(1).lower()} name={nombre}>")
    return faltantes


def test_hay_plantillas_para_revisar():
    """Si el glob se rompe, el barrido pasa en verde sin mirar nada."""
    assert len(_plantillas()) >= 40


def test_el_escaneo_encuentra_un_campo_pelado(tmp_path):
    """La mutación de sanidad, adentro del test: el detector detecta."""
    p = tmp_path / "x.html"
    p.write_text('<input type="text" name="q">', encoding="utf-8")
    assert _campos_sin_nombre(p), "el escaneo no ve un input sin etiqueta"

    p.write_text('<input type="text" name="q" aria-label="Buscar">', encoding="utf-8")
    assert not _campos_sin_nombre(p)

    p.write_text('<label for="q">Buscar</label><input id="q" name="q">', encoding="utf-8")
    assert not _campos_sin_nombre(p)

    p.write_text('<label>Buscar <input name="q"></label>', encoding="utf-8")
    assert not _campos_sin_nombre(p)


def test_el_escaneo_no_se_deja_enganar_por_un_comentario(tmp_path):
    """La trampa propia del repo: el comentario que explica el patrón lo contiene."""
    p = tmp_path / "x.html"
    p.write_text(
        '{# antes era <input type="text" name="q"> sin etiqueta #}\n'
        '<input type="text" name="q" aria-label="Buscar">',
        encoding="utf-8",
    )
    assert not _campos_sin_nombre(p), "el escaneo contó el campo que vive en un comentario"


@pytest.mark.parametrize("path", _plantillas(), ids=lambda p: p.name)
def test_todo_campo_tiene_nombre_accesible(path):
    faltantes = _campos_sin_nombre(path)
    assert not faltantes, (
        f"{path.name}: {len(faltantes)} campo(s) sin nombre accesible. "
        f"Va `<label for>` cuando ya hay una etiqueta visible —para que no "
        f"queden dos textos que se separen— y `aria-label` cuando no la hay:\n"
        + "\n".join(faltantes)
    )

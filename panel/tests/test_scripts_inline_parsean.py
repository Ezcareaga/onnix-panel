"""Todo `<script>` inline de una plantilla tiene que ser JavaScript valido.

**El bug que fija.** `base.html` definia la canasta de links de propiedades con
`Alpine.data("linkBasket", () => ({ ... }))`. Al sacar el vertical inmobiliario
(ea8af4e) se borro la CABECERA de ese bloque y quedaron los metodos sueltos
colgando de un `const LinkBasket = window.LinkBasket;`:

    document.addEventListener("alpine:init", () => {
      const LinkBasket = window.LinkBasket;
          return { ok: true, count: this.count, ... };   <-- huerfano
        },
        async copyAll() { ... },

Eso es un `SyntaxError: missing ) after argument list`. Y un SyntaxError **no
rompe una linea: no ejecuta el `<script>` entero**. Abajo del fragmento muerto
vivia `Alpine.data("fixedMenu", ...)`, que es el componente de TODOS los menus
desplegables del panel — el selector de estado de cada lead, el «Asignar a…»,
los menus de la tabla de usuarios.

Sin `fixedMenu` registrado, Alpine no puede evaluar `x-data="fixedMenu"`, y sin
`x-data` no aplica el `x-show`: los desplegables quedaban **todos abiertos a la
vez**, superpuestos sobre las filas. `/leads` era ilegible. Cada fila tiraba su
propio «Illegal invocation» — 115 errores en una sola carga.

`base.html` lo incluye TODO el panel, asi que el estropicio era global; se
noto en `/leads` porque es la pantalla con mas desplegables por fila.

**Por que no lo vio ningun test.** Los tests de plantilla de este repo miran el
HTML: que exista un atributo, que una clase este, que un token no diverja.
Ninguno le pregunta a un parser de JS si el script corre. Es la clase entera lo
que faltaba cubrir, no este bloque.

El chequeo usa `node --check`, que es un parser de verdad y no un regex. Si no
hay node en el entorno, el test se SKIPEA nombrando que falta — un skip mudo
seria peor que no tenerlo (trampa 3 del CLAUDE.md).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

# `<script>` sin `src=`: los que traen src son los vendor, que no compilamos.
_INLINE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)

_JINJA_EXPR = re.compile(r"\{\{.*?\}\}", re.S)
_JINJA_TAG = re.compile(r"\{%.*?%\}", re.S)
_JINJA_COMENT = re.compile(r"\{#.*?#\}", re.S)

_NODE = shutil.which("node")


def _bloques() -> list[tuple[Path, int, str]]:
    hallazgos = []
    for ruta in sorted(_TEMPLATES.rglob("*.html")):
        texto = ruta.read_text(encoding="utf-8")
        for n, cuerpo in enumerate(_INLINE.findall(texto), start=1):
            if cuerpo.strip():
                hallazgos.append((ruta, n, cuerpo))
    return hallazgos


def _sin_jinja(cuerpo: str) -> str:
    """Deja JS parseable.

    `{{ x }}` pasa a un string y no a nada: en `const a = {{ x }};` borrarlo
    dejaria `const a = ;`, que es un SyntaxError inventado por el test.
    """
    cuerpo = _JINJA_COMENT.sub("", cuerpo)
    cuerpo = _JINJA_TAG.sub("", cuerpo)
    return _JINJA_EXPR.sub('"J"', cuerpo)


_BLOQUES = _bloques()


def test_hay_scripts_para_revisar():
    """Si el regex se rompe, el test de abajo pasa sobre una lista vacia."""
    assert len(_BLOQUES) >= 5, (
        f"solo {len(_BLOQUES)} bloques inline encontrados — el regex dejo de "
        "matchear y este archivo se volvio decorativo"
    )


@pytest.mark.skipif(_NODE is None, reason="falta `node` en el PATH: sin parser de JS no se puede chequear")
@pytest.mark.parametrize(
    "ruta,indice,cuerpo",
    _BLOQUES,
    ids=[f"{r.relative_to(_TEMPLATES)}#{i}" for r, i, _ in _BLOQUES],
)
def test_el_script_inline_parsea(ruta, indice, cuerpo):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(_sin_jinja(cuerpo))
        tmp = f.name
    try:
        r = subprocess.run([_NODE, "--check", tmp], capture_output=True, text=True)
    finally:
        os.unlink(tmp)

    assert r.returncode == 0, (
        f"el <script> #{indice} de {ruta.relative_to(_TEMPLATES)} no parsea. Un "
        f"SyntaxError no rompe una linea: deja el bloque ENTERO sin ejecutar, "
        f"y con el todo lo que ese bloque define.\n{r.stderr.strip()}"
    )


def test_fixedMenu_sigue_definido():
    """El componente de todos los desplegables del panel.

    El test de arriba caza un bloque que no parsea; este caza que el bloque
    parsee pero se haya quedado sin lo que importa.
    """
    base = (_TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert 'Alpine.data("fixedMenu"' in base, (
        "se fue `fixedMenu` de base.html: sin el, todo `x-data=\"fixedMenu\"` "
        "queda sin definir y los desplegables se renderizan abiertos"
    )

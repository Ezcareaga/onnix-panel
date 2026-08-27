"""Los template_key hardcodeados en los Jinja tienen que existir en el allowlist.

Nació de un bug real: `partials/conversation_thread.html` mandaba
`wa_tpl_followup`, borrado del allowlist en `01f613b` (2026-04-20). Como el
form es un POST plano y la ruta devuelve el parcial de error con status 200,
el navegador renderizaba ese fragmento como documento entero: la administradora
quedaba en una página en blanco con el `str()` de un error de Pydantic, y el
botón era el único modo de contactar a alguien con la ventana de 24 h vencida.

No toca la base ni la app: es una lectura de archivos.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.schemas.template import ALLOWED_TEMPLATE_KEYS

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"

# <input type="hidden" name="template_key" value="wa_tpl_x">, en cualquier
# orden de atributos, comillas simples o dobles.
_PATTERN = re.compile(
    r"""name=["']template_key["'][^>]*?value=["']([^"']+)["']"""
    r"""|value=["']([^"']+)["'][^>]*?name=["']template_key["']""",
)


def _hardcoded_keys() -> list[tuple[str, int, str]]:
    """(ruta relativa, nro de línea, key) por cada template_key literal."""
    found = []
    for path in sorted(TEMPLATES_DIR.rglob("*.html")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _PATTERN.finditer(line):
                key = match.group(1) or match.group(2)
                found.append((str(path.relative_to(TEMPLATES_DIR)), lineno, key))
    return found


def test_hay_al_menos_un_template_key_hardcodeado():
    """Si el regex deja de matchear, el test de abajo pasa vacío y no protege nada."""
    assert _hardcoded_keys(), "El patrón no encontró ningún template_key; revisá el regex"


@pytest.mark.parametrize("relpath,lineno,key", _hardcoded_keys())
def test_template_key_esta_en_el_allowlist(relpath, lineno, key):
    assert key in ALLOWED_TEMPLATE_KEYS, (
        f"{relpath}:{lineno} manda template_key={key!r}, que no está en "
        f"ALLOWED_TEMPLATE_KEYS. El POST va a fallar con un error de Pydantic "
        f"renderizado como página en blanco."
    )

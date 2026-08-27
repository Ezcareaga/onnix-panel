"""Leer una plantilla pública **con sus includes resueltos**.

Las dos páginas públicas —la ficha y el listado— repartían su CSS en dos
lugares desde el 2026-08-23: lo que comparten vive en
`public/_estilos_comunes.html` y cada una declara lo suyo. Un test que abre
`property.html` y busca `.wordmark` ahí ya no lo encuentra, aunque el navegador
lo reciba igual.

**Eso puso 19 tests en rojo de una**, y el detalle importa: los tres archivos de
test tenían su propio lector —`_css`, y dos `_sin_comentarios(...read_text())`—
que hacían lo mismo con tres nombres. Tres copias es como se rompen juntas. El
lector vive acá y en ningún otro lado.

No usa Jinja para resolver el include a propósito: `Environment.get_template()`
también evalúa las variables, y estos tests quieren el **texto** de la
plantilla, no su render. Un `{{ prop.title }}` tiene que seguir estando.
"""
from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
PUBLIC = TEMPLATES / "public"

_INCLUDE = re.compile(r"\{%-?\s*include\s+[\"']([^\"']+)[\"']\s*-?%\}")
_COMENTARIO_CSS = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMENTARIO_JINJA = re.compile(r"\{#.*?#\}", re.DOTALL)
_COMENTARIO_HTML = re.compile(r"<!--.*?-->", re.DOTALL)

_MAX_PROFUNDIDAD = 5


def con_includes(path: Path, _profundidad: int = 0) -> str:
    """El texto de la plantilla con cada `{% include %}` reemplazado por el suyo.

    Recursivo, con tope: un include circular colgaría el test en vez de
    fallarlo, y un test que cuelga es peor que uno que falla.
    """
    if _profundidad > _MAX_PROFUNDIDAD:
        raise RecursionError(
            f"{path.name}: más de {_MAX_PROFUNDIDAD} niveles de include. "
            "¿Hay un ciclo?"
        )
    texto = path.read_text(encoding="utf-8")

    def reemplazar(m: re.Match) -> str:
        incluido = TEMPLATES / m.group(1)
        if not incluido.exists():
            raise FileNotFoundError(
                f"{path.name} incluye {m.group(1)}, que no existe"
            )
        return con_includes(incluido, _profundidad + 1)

    return _INCLUDE.sub(reemplazar, texto)


def sin_comentarios(texto: str) -> str:
    """Sin comentarios de CSS, de Jinja ni de HTML.

    Va siempre después de resolver los includes, y no antes: el comentario que
    explica una regla nombra lo que la regla prohíbe, y en este repo eso ya dejó
    pasar el borrado de un color.
    """
    texto = _COMENTARIO_CSS.sub(" ", texto)
    texto = _COMENTARIO_JINJA.sub(" ", texto)
    return _COMENTARIO_HTML.sub(" ", texto)


def fuente_de(path: Path) -> str:
    """Lo que los tests quieren mirar: includes adentro, comentarios afuera."""
    return sin_comentarios(con_includes(path))

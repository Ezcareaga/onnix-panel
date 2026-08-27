"""Un solo estado vacío para todo el panel. M5 del carril M.

`partials/empty_state.html` existe desde el carril J y lo usaba **una sola
pantalla**. El resto escribía el suyo: la fila de leads una frase suelta en un
`<td>`, las conversaciones del contacto su propio icono de 40px con su propia
jerarquía, las visitas otro más. Con HTMX se nota más que en una SPA: dos
`hx-swap` que resuelven el mismo estado con markup distinto se ven al instante
cuando uno reemplaza al otro.

**Por qué no se había propagado, que no es «faltó tiempo»:** el macro
renderizaba siempre su caja blanca, y la mitad de los estados vacíos del panel
no son de página sino **de sección** — viven adentro de un `<td>`, de un
`<details>` o de una card. Meterles la caja los volvía cards adentro de cards,
que `ui.md` prohíbe por nombre. De ahí sale `superficie=false`: cambia la caja,
nunca el criterio.

Lo que este test **no** exige: que los «Sin datos» de las cards de métricas
usen el macro. Son una línea adentro de un gráfico de 200px, no un estado
vacío de sección; meterles icono y botón sería ruido. Si alguna vez se
unifican, es con otro patrón, no con este.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

_COMENT_JINJA = re.compile(r"\{#.*?#\}", re.S)
_COMENT_HTML = re.compile(r"<!--.*?-->", re.S)

# Las pantallas donde el vacío ya está unificado. La lista es explícita a
# propósito: dice qué cubre el barrido, así que agregar una pantalla nueva sin
# su estado vacío no pasa desapercibido por omisión.
_CON_ESTADO_VACIO = [
    "contacts.html",
    "contacts_detail.html",
    "stats.html",
    "partials/leads_table.html",
    "partials/visits_block.html",
    "partials/crm_followup.html",
    "partials/conversation_list.html",
    "properties/partials/properties_table.html",
]


def _sin_comentarios(path: Path) -> str:
    return _COMENT_HTML.sub("", _COMENT_JINJA.sub("", path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("rel", _CON_ESTADO_VACIO)
def test_la_pantalla_usa_el_parcial_y_no_su_propio_markup(rel):
    src = _sin_comentarios(_TEMPLATES / rel)
    assert "empty_state.html" in src, (
        f"{rel} dejó de importar el parcial de estado vacío: o lo perdió, o "
        f"volvió a escribir el suyo"
    )
    assert re.search(r"\bvacio\(", src), f"{rel} importa el macro y no lo llama"


def _render(**kw) -> str:
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=True)
    tpl = env.from_string(
        '{% from "partials/empty_state.html" import vacio %}{{ vacio(*args, **kw) }}'
    )
    return tpl.render(args=kw.pop("args", []), kw=kw)


def test_con_superficie_trae_la_caja():
    html = _render(args=["Sin nada"])
    assert "data-empty-state" in html
    assert "bg-white" in html and "shadow" in html


def test_sin_superficie_no_trae_la_caja():
    """Adentro de un <td> o de un <details> la caja sería card dentro de card."""
    html = _render(args=["Sin nada"], superficie=False)
    assert "data-empty-state" in html
    assert "bg-white" not in html, "la caja sigue puesta con superficie=false"
    assert "shadow" not in html


def test_el_criterio_no_cambia_con_la_superficie():
    """Lo que cambia es la caja: el título, el detalle y la acción no."""
    con = _render(args=["Sin nada", "Un detalle"], href="/x", cta="Ir")
    sin = _render(args=["Sin nada", "Un detalle"], href="/x", cta="Ir", superficie=False)
    for pedazo in ("Sin nada", "Un detalle", 'href="/x"', ">Ir<", "min-h-[44px]",
                   "data-empty-action"):
        assert pedazo in con, f"falta {pedazo!r} con superficie"
        assert pedazo in sin, f"falta {pedazo!r} sin superficie"


def test_el_icono_va_solo_con_la_caja():
    """Es una casa: en «sin conversaciones» o «sin notas» dice propiedad, no vacío.

    Y una ilustración adentro de una card ya la hace leer como card dentro de
    card aunque la caja no esté.
    """
    assert "<svg" in _render(args=["Sin nada"])
    assert "<svg" not in _render(args=["Sin nada"], superficie=False)


def test_la_accion_es_opcional_pero_no_se_rompe_a_medias():
    """Con `href` y sin `cta` no se emite un link sin texto."""
    assert "data-empty-action" not in _render(args=["Sin nada"], href="/x")
    assert "data-empty-action" not in _render(args=["Sin nada"], cta="Ir")
    assert "data-empty-action" in _render(args=["Sin nada"], href="/x", cta="Ir")

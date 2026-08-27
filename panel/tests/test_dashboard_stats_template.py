"""El dashboard no imprime porcentajes que no son porcentajes.

La card "Contactos por estado" mostraba una "Tasa de cierre" que dividia
`closed / new` — dos conteos de estado ACTUAL, no una cohorte — y sin clamp.
Con 40 cerrados y 2 nuevos daba 2.000%, y el numero mas grande de la pantalla
era el unico sin significado.

El test no busca el texto que se borro: busca la clase de error. Renderiza el
parcial con el caso exacto que lo delataba y falla si aparece CUALQUIER
porcentaje mayor a 100. Asi cubre tambien la proxima division sin clamp que
alguien agregue.

(`demand_section.html` se stubeaba acá; se fue con el vertical inmobiliario)
con su propio contexto, y no es lo que este test mide.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

_PANEL = Path(__file__).resolve().parent.parent
if str(_PANEL) not in sys.path:
    sys.path.insert(0, str(_PANEL))

_TEMPLATES = _PANEL / "app" / "templates"

# 40 cerrados contra 2 nuevos: el caso que daba 2.000%.
_STATS = {
    "lead_tab_counts": {"sin_respuesta": 3, "interesados": 1},
    "total_leads": 120,
    "new_today": 2,
    "bot_enabled": True,
    "messages_24h": 40,
    "errors_24h": 0,
    "status_counts": {
        "new": 2,
        "bot_replied": 5,
        "agent_replied": 4,
        "interested": 6,
        "closed": 40,
        "no_response": 3,
        "discarded": 1,
    },
}

_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


@pytest.fixture(scope="module")
def rendered() -> str:
    env = Environment(
        loader=ChoiceLoader([
            FileSystemLoader(str(_TEMPLATES)),
        ]),
        autoescape=True,
    )
    return env.get_template("partials/dashboard_stats.html").render(stats=_STATS)


def test_ningun_porcentaje_supera_el_100(rendered: str):
    fuera_de_rango = [
        m.group(0) for m in _PCT_RE.finditer(rendered)
        if float(m.group(1).replace(",", ".")) > 100.0
    ]
    assert not fuera_de_rango, (
        "porcentajes imposibles en el dashboard: "
        f"{fuera_de_rango} — con status_counts={_STATS['status_counts']}"
    )


def test_el_total_de_contactos_sigue_estando(rendered: str):
    """Lo que se borro es la tasa, no el conteo que la acompanaba."""
    assert "Total contactos" in rendered
    assert ">61<" in rendered.replace(" ", "").replace("\n", "")


# ===========================================================================
# Carril I — los dos totales de la columna, y la tinta adentro de la barra
# ===========================================================================
#
# Eran dos numeros distintos con la misma pinta: «Total leads» sumaba
# status_counts entero —con los `deleted` adentro— y «Total contactos» sumaba
# solo las filas que el embudo listaba, que eran siete de los ocho estados
# vivos. Los `visit_scheduled`, vivos desde la migracion 040 y sincronizados
# por VisitService, no aparecian en ninguna fila y desaparecian del embudo.
#
# El arreglo no fue emparejar los dos numeros: fue que el embudo cubra
# VALID_STATUSES entero y que count_by_status deje afuera el sentinel de
# borrado. Estos tests fijan las dos mitades.
#
# Y de paso: cinco de las siete barras decian su numero por debajo de 3:1.
# El piso se calcula acá contra el CSS que sirve la app, no se copia de un
# comentario — dos numeros escritos a mano en la landing decian 5,79 y 2,89
# y eran 11,30 y 5,65.

from app.constants import VALID_STATUSES

_CSS = _PANEL / "app" / "static" / "css" / "tailwind.css"
_PARCIAL = _TEMPLATES / "partials" / "dashboard_stats.html"

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)

# ('clave', 'etiqueta', 'bg-...', 'text-...')
_FILA_RE = re.compile(
    r"\(\s*'([a-z_]+)'\s*,\s*'([^']*)'\s*,\s*'(bg-[\w-]+)'\s*,\s*'(text-[\w-]+)'\s*\)"
)


def _sin_comentarios(texto: str) -> str:
    """El comentario que explica el arreglo nombra las clases que arregla."""
    return _HTML_COMMENT.sub("", _JINJA_COMMENT.sub("", texto))


def _filas_del_embudo() -> list[tuple[str, str, str, str]]:
    filas = _FILA_RE.findall(_sin_comentarios(_PARCIAL.read_text(encoding="utf-8")))
    assert filas, "no se pudo leer funnel_data del parcial"
    return filas


def _srgb(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminancia(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def _contraste(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _luminancia(a), _luminancia(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _color_de_la_clase(clase: str) -> tuple[int, int, int]:
    """El color que sirve la app, leido del CSS compilado.

    Una clase de color NOMBRADA que no exista en el CSS no falla y no pinta
    nada, en silencio: por eso no alcanza con que este en tailwind.config.js.
    """
    css = _CSS.read_text(encoding="utf-8")
    m = re.search(
        r"\." + re.escape(clase) + r"\{[^}]*?rgb\((\d+)\s+(\d+)\s+(\d+)",
        css,
    )
    assert m, (
        f"la clase .{clase} no esta en el CSS compilado: no pinta nada y no "
        f"da error. Si es nueva, tiene que aparecer en el build de Tailwind."
    )
    return tuple(int(g) for g in m.groups())


def test_el_embudo_cubre_todos_los_estados_vivos():
    """Un estado sin fila desaparece del embudo y descuadra el total.

    Es lo que pasaba con visit_scheduled: la migracion 040 lo reintrodujo,
    VisitService lo escribe, y el dashboard no lo mostraba en ninguna parte.
    """
    claves = {fila[0] for fila in _filas_del_embudo()}
    faltan = VALID_STATUSES - claves
    sobran = claves - VALID_STATUSES
    assert not faltan, f"estados vivos sin fila en el embudo: {sorted(faltan)}"
    assert not sobran, f"filas del embudo que no son estados validos: {sorted(sobran)}"


def test_el_embudo_no_lista_los_eliminados():
    """`deleted` es el sentinel del borrado blando, no una etapa del embudo."""
    assert "deleted" not in {fila[0] for fila in _filas_del_embudo()}


def test_los_dos_totales_son_el_mismo_numero():
    """«Total contactos» arriba y el pie del embudo cuentan el mismo universo.

    Se renderiza con lo que devuelve el servicio de verdad: total_leads es
    sum(status_counts.values()) sobre lo que da count_by_status.
    """
    conteos = {clave: n for n, clave in enumerate(sorted(VALID_STATUSES), start=3)}
    stats = dict(_STATS, status_counts=conteos, total_leads=sum(conteos.values()))
    env = Environment(
        loader=ChoiceLoader([
            FileSystemLoader(str(_TEMPLATES)),
        ]),
        autoescape=True,
    )
    html = env.get_template("partials/dashboard_stats.html").render(stats=stats)
    apariciones = re.findall(r">\s*(\d+)\s*<", html)
    total = str(sum(conteos.values()))
    assert apariciones.count(total) >= 2, (
        f"el total {total} tendria que aparecer arriba y al pie del embudo; "
        f"se imprimieron {apariciones.count(total)} veces. "
        f"Numeros renderizados: {apariciones}"
    )


def test_cada_barra_dice_su_numero_por_encima_del_piso_de_45():
    """Cinco de las siete barras estaban por debajo de 3:1.

    El numero vive adentro de la barra, asi que la tinta se mide contra el
    fondo de la barra, no contra la superficie de la card.
    """
    flojas = []
    for clave, _etiqueta, bg, txt in _filas_del_embudo():
        ratio = _contraste(_color_de_la_clase(bg), _color_de_la_clase(txt))
        if ratio < 4.5:
            flojas.append(f"{clave}: {txt} sobre {bg} = {ratio:.2f}:1")
    assert not flojas, "barras del embudo por debajo de 4,5:1:\n  " + "\n  ".join(flojas)

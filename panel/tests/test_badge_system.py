"""Carril B3 — el badge de estado pierde el color (decision de Ez, 2026-08-17).

Eran 11 geometrias y 12 matices en 158 usos solo en CRM, y ui.md:87 admite
como mucho dos matices saturados en toda la interfaz: el estado del lead se
comia los dos, dejando sin senal al precio y a lo que de verdad urge.

Ahora son cuatro variantes y lo que las distingue es el peso visual, no el
tono. Cuanto mas oscuro, mas exige.

Y el mapa vive UNA sola vez. Antes estaba en app/constants.py y otra vez en
partials/status_badge.html, con un comentario en cada archivo pidiendo
sincronizarlos a mano. Se desincronizaron igual: contacts.html reimplementaba
el badge de fuente inline y ahi `manual` era morado, contra el gris del
parcial.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.constants import BADGE_MAP, VALID_STATUSES

_PANEL = Path(__file__).resolve().parent.parent
_TEMPLATES = _PANEL / "app" / "templates"
_CSS = (_PANEL / "app" / "static" / "css" / "custom.css").read_text(encoding="utf-8")

VARIANTES = {"default", "quiet", "strong", "danger"}


def test_las_cuatro_variantes_existen_en_el_css():
    definidas = set(re.findall(r"\.badge--(\w+)", _CSS))
    assert definidas == VARIANTES, definidas


def test_el_mapa_guarda_variante_y_etiqueta():
    for estado, valor in BADGE_MAP.items():
        assert len(valor) == 2, f"{estado} sigue guardando clases de Tailwind: {valor}"
        variante, etiqueta = valor
        assert variante in VARIANTES, f"{estado} usa la variante {variante!r}"
        assert etiqueta, f"{estado} sin etiqueta"


def test_el_mapa_cubre_todos_los_estados_validos():
    assert VALID_STATUSES <= set(BADGE_MAP), VALID_STATUSES - set(BADGE_MAP)


def test_los_terminales_retroceden():
    """Un lead cerrado no compite por atencion con uno que espera respuesta."""
    for estado in ("closed", "no_response", "discarded", "deleted"):
        assert BADGE_MAP[estado][0] == "quiet", f"{estado} no es quiet"


def test_lo_que_exige_accion_es_lo_mas_oscuro():
    for estado in ("new", "interested"):
        assert BADGE_MAP[estado][0] == "strong", f"{estado} no es strong"


def test_ningun_estado_usa_danger():
    """ui.md reserva el rojo para destructivo e irreversible. Ningun estado del
    contacto lo es: `deleted` es soft-delete (is_active = FALSE)."""
    assert not [e for e, (v, _) in BADGE_MAP.items() if v == "danger"]


def test_el_mapa_no_esta_duplicado_en_jinja():
    parcial = (_TEMPLATES / "partials" / "status_badge.html").read_text(encoding="utf-8")
    assert "{% set badge_map" not in parcial, (
        "status_badge.html volvio a tener su propia copia del mapa"
    )
    assert "badge_map.get(" in parcial


def test_el_oro_no_aparece_en_ningun_badge():
    bloque = _CSS[_CSS.index(".badge {"):_CSS.index(".badge--danger")]
    for prohibido in ("--accent", "onnix-accent", "#16181A"):
        assert prohibido not in bloque, f"el badge usa {prohibido}"


@pytest.mark.parametrize(
    "clase",
    ["bg-emerald-100", "bg-purple-100", "bg-indigo-100", "bg-amber-100",
     "bg-blue-100", "bg-orange-100", "bg-yellow-100"],
)
def test_no_vuelven_los_matices_por_estado_en_crm(clase):
    for rel in ("contacts.html", "partials/lead_item.html",
                "partials/status_badge.html", "partials/source_badge.html"):
        contenido = (_TEMPLATES / rel).read_text(encoding="utf-8")
        assert clase not in contenido, f"{rel} volvio a usar {clase}"

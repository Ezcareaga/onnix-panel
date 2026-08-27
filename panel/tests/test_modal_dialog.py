"""Los modales usan <dialog> nativo, no un <div> con position:fixed.

El audit encontro cero `role="dialog"` en los 61 templates y ningun focus
trap: con el modal abierto, el Tab seguia recorriendo la pagina de atras.
Cerrar tocando el fondo funcionaba en 2 de 7.

`<dialog>` + `showModal()` da las cuatro cosas de una, y las da el navegador:
focus trap, Escape, `::backdrop`, y el resto del documento inertizado. No hay
JavaScript propio que mantener ni que testear — por eso el test mira la
estructura, no el comportamiento: el comportamiento es del navegador.

Alpine sigue mandando el estado para no tocar los disparadores que ya existen.
Eso deja un detalle que SI hay que testear: el Escape del navegador cierra el
<dialog> sin avisarle a Alpine, asi que sin un `@close` que devuelva la
variable a false, el modal no vuelve a abrir nunca mas.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
_CSS = (_TEMPLATES.parent / "static" / "css" / "custom.css").read_text(encoding="utf-8")

# Los que ya estan convertidos. Se van sumando a medida que se migran.
# Falta send_template_drawer.html. El carril D ya le saco las ~126 lineas
# muertas; la conversion a <dialog> queda para el carril H, que reescribe
# Conversaciones entera y es donde el drawer cambia de forma.
_CONVERTIDOS = [
    "contacts.html",
    "settings.html",
    "partials/auth_audit_table.html",
    "partials/visit_create_modal.html",
    "partials/visit_reschedule_modal.html",
]


@pytest.mark.parametrize("rel", _CONVERTIDOS)
class TestDialogNativo:
    def _html(self, rel: str) -> str:
        return (_TEMPLATES / rel).read_text(encoding="utf-8")

    def test_usa_dialog(self, rel):
        assert "<dialog" in self._html(rel)

    def test_se_abre_con_show_modal(self, rel):
        """`show()` no da focus trap ni ::backdrop; `showModal()` si."""
        html = self._html(rel)
        assert "showModal()" in html
        assert re.search(r"\$el\.show\(\)", html) is None, (
            "show() abre el dialog sin modalidad: sin focus trap ni backdrop"
        )

    def test_devuelve_el_estado_a_alpine_al_cerrar(self, rel):
        """El Escape del navegador cierra el <dialog> sin pasar por Alpine.
        Sin este handler la variable queda en true y el modal no reabre."""
        assert '@close="' in self._html(rel)

    def test_tiene_nombre_accesible(self, rel):
        html = self._html(rel)
        assert "aria-labelledby=" in html or "aria-label=" in html

    def test_no_quedo_el_overlay_a_mano(self, rel):
        """`::backdrop` lo pinta el navegador. Un overlay propio se superpone
        y vuelve a romper el click-fuera."""
        html = self._html(rel)
        bloque = html[html.index("<dialog"):]
        for overlay in ("bg-black bg-opacity-50", "bg-black/40", "fixed inset-0"):
            assert overlay not in bloque, f"overlay a mano sobreviviente: {overlay}"

    def test_no_quedo_el_escape_a_mano(self, rel):
        """El Escape lo cierra el navegador. El handler propio ademas escuchaba
        en `window`, asi que un Escape con el modal cerrado igual corria."""
        assert "@keydown.escape.window" not in self._html(rel)


def test_el_titulo_por_fila_lleva_un_id_unico():
    """El modal de reagendar se renderiza una vez por visita. Un id repetido
    deja el aria-labelledby de todas las filas apuntando a la primera."""
    html = (_TEMPLATES / "partials" / "visit_reschedule_modal.html").read_text(
        encoding="utf-8"
    )
    assert 'aria-labelledby="visit-reschedule-title-{{ v.id }}"' in html


def test_el_desbloqueo_no_cierra_el_modal_cuando_falla():
    """Cerraba pase lo que pase: un 4xx se llevaba el modal y el desbloqueo
    fallido quedaba invisible."""
    html = (_TEMPLATES / "partials" / "auth_audit_table.html").read_text(
        encoding="utf-8"
    )
    assert '@htmx:after-request="if ($event.detail.successful) unlockTarget = null"' in html


def test_el_css_del_modal_existe():
    """El default de <dialog> es un margin auto que lo pega arriba."""
    assert ".modal {" in _CSS
    assert ".modal::backdrop" in _CSS
    assert "dialog:modal" in _CSS

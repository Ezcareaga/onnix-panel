"""Carril B5 — un solo patron de error, y ninguno que se coma la pantalla.

Dos bugs distintos:

1. Los handlers de 404 y 500 devolvian el documento HTML entero sin mirar si
   la peticion venia de HTMX. Un 404 sobre un <tr> inyectaba un
   `<!DOCTYPE html>` completo adentro de la tabla. Reproducible desde
   `user_row.html:18` con un id borrado.
2. La pagina de 500 decia «el equipo tecnico ya fue notificado». Es mentira:
   no hay Sentry ni webhook. Ahora no promete nada y entrega un codigo de
   correlacion, que es lo que hace accionable un «avisale a Ez».
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PANEL = Path(__file__).resolve().parent.parent
_TEMPLATES = _PANEL / "app" / "templates"
_MAIN = (_PANEL / "app" / "main.py").read_text(encoding="utf-8")


def test_las_dos_paginas_de_error_son_una():
    assert (_TEMPLATES / "error.html").exists()
    assert not (_TEMPLATES / "error_404.html").exists()
    assert not (_TEMPLATES / "error_500.html").exists()


def test_los_handlers_ramifican_por_hx_request():
    """Sin esto un <!DOCTYPE html> entero entra adentro de un <tr>."""
    assert "_es_htmx" in _MAIN
    assert _MAIN.count("_es_htmx(request)") >= 2


def test_la_pagina_de_500_no_promete_una_notificacion_que_no_existe():
    error_html = (_TEMPLATES / "error.html").read_text(encoding="utf-8")
    for frase in ("ya fue notificado", "equipo tecnico", "equipo técnico"):
        assert frase not in error_html, (
            f"«{frase}» vuelve a prometer algo que no pasa: no hay Sentry ni webhook"
        )


def test_el_500_entrega_un_codigo_de_correlacion():
    """Sin un id, «avisale a Ez» es inaccionable: no hay como encontrar ESTE
    error entre los miles de renglones de log del dia."""
    assert "correlacion = uuid.uuid4()" in _MAIN
    assert "correlacion=%s" in _MAIN, "el id tiene que quedar en el log"
    assert "{{ correlacion }}" in (_TEMPLATES / "error.html").read_text(encoding="utf-8")


def test_el_500_ofrece_reintentar():
    """Un 500 suele ser transitorio; mandar al dashboard pierde el trabajo."""
    assert "Reintentar" in (_TEMPLATES / "error.html").read_text(encoding="utf-8")


class TestParcialDeError:
    _PARCIAL = _TEMPLATES / "partials" / "error_message.html"

    def test_tiene_role(self):
        contenido = self._PARCIAL.read_text(encoding="utf-8")
        assert 'role="{{ _e.rol }}"' in contenido

    @pytest.mark.parametrize("nivel", ["error", "warning", "info"])
    def test_tiene_las_tres_variantes(self, nivel):
        assert f"'{nivel}':" in self._PARCIAL.read_text(encoding="utf-8")

    def test_puede_ofrecer_reintento(self):
        assert "retry_url" in self._PARCIAL.read_text(encoding="utf-8")

    def test_ya_no_usa_el_rojo_que_no_pasa(self):
        """text-red-500 sobre blanco da 3,76:1, contra un piso de 4,5."""
        assert "text-red-500" not in self._PARCIAL.read_text(encoding="utf-8")


def test_el_403_de_csrf_no_es_un_p_pelado():
    """Era `<p>Solicitud invalida (CSRF check)</p>`: sin estilo, sin role, y
    con la causa tecnica adelante en vez de que hacer."""
    assert 'error-msg error-msg--error' in _MAIN
    assert "CSRF check</p>" not in _MAIN

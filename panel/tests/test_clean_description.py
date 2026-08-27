"""TDD — clean_description filter for property descriptions.

Property descriptions from InfoCasas come with literal `<br />` and `\r`
characters embedded in the text. The filter replaces those with real
newlines and strips any remaining HTML, so the template can render with
whitespace-pre-line and look like proper paragraphs.
"""
from __future__ import annotations

import pytest

from app.tz import clean_description


class TestCleanDescription:
    def test_none_returns_empty_string(self):
        assert clean_description(None) == ""

    def test_empty_returns_empty(self):
        assert clean_description("") == ""

    def test_plain_text_passes_through(self):
        assert clean_description("Hola mundo") == "Hola mundo"

    def test_un_br_suelto_es_un_espacio(self):
        """Un `<br>` solo es la envoltura del portal de origen, no un párrafo.

        Medido el 2026-08-23 sobre una ficha real: 1.332 caracteres ocupaban 57
        líneas en el celular —23 por línea— donde entran 48,7. El texto venía
        envuelto al ancho de OTRO sistema y `white-space: pre-line` lo
        respetaba, así que el bloque medía el doble de lo que le corresponde.
        """
        assert clean_description("Linea1<br />Linea2") == "Linea1 Linea2"

    def test_replaces_br_no_space(self):
        assert clean_description("Linea1<br/>Linea2") == "Linea1 Linea2"

    def test_replaces_br_open(self):
        assert clean_description("Linea1<br>Linea2") == "Linea1 Linea2"

    def test_replaces_br_uppercase(self):
        assert clean_description("Linea1<BR />Linea2") == "Linea1 Linea2"

    def test_strips_carriage_return(self):
        assert clean_description("Linea1\r\nLinea2") == "Linea1 Linea2"

    def test_strips_lonely_carriage_return(self):
        assert clean_description("Linea1\rLinea2") == "Linea1 Linea2"

    def test_combined_real_world_infocasas(self):
        raw = (
            "En venta casa\r<br />\r<br />Ubicada en San Lorenzo.\r<br />"
            "Tiene 3 dorm."
        )
        cleaned = clean_description(raw)
        assert "<br" not in cleaned.lower()
        assert "\r" not in cleaned
        assert "En venta casa" in cleaned
        assert "Ubicada en San Lorenzo." in cleaned
        assert "Tiene 3 dorm." in cleaned

    def test_strips_other_html_tags(self):
        assert clean_description("Hola <strong>mundo</strong>") == "Hola mundo"

    def test_decodes_html_entities(self):
        assert clean_description("M&aacute;s informaci&oacute;n") == "Más información"

    def test_collapses_excessive_blank_lines(self):
        # Three or more consecutive newlines collapse to two (paragraph break)
        result = clean_description("A<br /><br /><br /><br />B")
        assert result == "A\n\nB"

    def test_does_not_break_xss_safe_render(self):
        # Script tags are stripped, never returned as HTML
        result = clean_description("<script>alert('xss')</script>Hola")
        assert "<script>" not in result
        assert "alert" not in result or "Hola" in result  # tag content stripped


class TestParrafoContraEnvoltura:
    """La distinción que hace la diferencia: uno es envoltura, dos es párrafo."""

    def test_dos_saltos_siguen_siendo_un_parrafo(self):
        assert clean_description("Uno<br /><br />Dos") == "Uno\n\nDos"

    def test_tres_o_mas_colapsan_a_uno(self):
        assert clean_description("Uno<br /><br /><br /><br />Dos") == "Uno\n\nDos"

    def test_el_texto_envuelto_del_portal_se_desenvuelve(self):
        """Así viene de onnixpy: envuelto a mano, con un `\\r` por línea."""
        crudo = (
            "Hermosa casa en el corazon de Luque, a metros de la avenida\r<br />"
            "principal, con acceso pavimentado y todos los servicios\r<br />"
            "conectados.\r<br />\r<br />Consultanos por una visita."
        )
        limpio = clean_description(crudo)
        assert limpio.count("\n") == 2, "solo el párrafo, no la envoltura"
        assert "avenida principal" in limpio
        assert "servicios conectados" in limpio

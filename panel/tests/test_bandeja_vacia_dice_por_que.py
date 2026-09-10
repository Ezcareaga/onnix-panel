"""El vacío de la bandeja tiene que decir POR QUÉ está vacío.

`conversation_list.html` mostraba «No hay conversaciones todavía / Cuando
alguien escriba por WhatsApp, Instagram o Messenger, aparece acá» en TODOS los
casos sin filas. Con 50 hilos de WhatsApp cargados, filtrar a Instagram vaciaba
la lista y el panel contestaba que nunca escribió nadie.

Eso no se lee como «este filtro no tiene resultados»: se lee como que la
pantalla se rompió. Fue el reporte textual de Ez —«al cambiar de filtro de
whatsapp a ig desaparece todo»— y el motivo por el que parecía un bug aunque
el filtro anduviera bien.

`ui.md`: «Estados vacíos con acción, no disculpas». Un vacío que produjo el
propio usuario tiene que ofrecerle deshacerlo; el de primer arranque no tiene
nada que ofrecer y por eso es el único sin CTA.
"""
from __future__ import annotations

import re

import pytest

from app.tz import get_templates

_PARCIAL = "partials/conversation_list.html"


def _render(**kwargs) -> str:
    """La lista SIN conversaciones, que es el caso que importa."""
    ctx = {
        "conversations": [],
        "offset": 0,
        "channel": "",
        "q": "",
        "stuck": None,
        "has_more": False,
        "limit": 50,
        "selected_id": None,
        "user": None,
    }
    ctx.update(kwargs)
    return get_templates().env.get_template(_PARCIAL).render(**ctx)


def _texto(html: str) -> str:
    sin_tags = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", sin_tags).strip()


class TestElVacioNombraSuCausa:
    @pytest.mark.parametrize("canal,etiqueta", [
        ("whatsapp", "WhatsApp"),
        ("instagram", "Instagram"),
        ("messenger", "Messenger"),
    ])
    def test_filtrar_por_canal_nombra_el_canal(self, canal, etiqueta):
        t = _texto(_render(channel=canal))
        assert etiqueta in t, f"el vacío de {canal} no nombra el canal: {t[:120]}"
        assert "No hay conversaciones todavía" not in t, (
            "sigue diciendo el mensaje de primer arranque con un filtro puesto: "
            "con hilos en otros canales, eso es mentira"
        )

    def test_sin_filtros_sigue_el_mensaje_de_primer_arranque(self):
        """El caso legítimo: la bandeja de verdad está vacía."""
        t = _texto(_render())
        assert "No hay conversaciones todavía" in t

    def test_buscar_sin_resultados_repite_lo_buscado(self):
        t = _texto(_render(q="perez"))
        assert "perez" in t, "no dice qué se buscó, así que no se puede corregir"

    def test_trabadas_conserva_su_mensaje(self):
        t = _texto(_render(stuck="1"))
        assert "Está todo contestado" in t


class TestElVacioQueSePuedeDeshacerOfreceDeshacerlo:
    """ui.md: estados vacíos con acción, no disculpas."""

    @pytest.mark.parametrize("kwargs", [
        {"channel": "instagram"},
        {"q": "perez"},
        {"q": "perez", "channel": "whatsapp"},
    ])
    def test_hay_salida_del_filtro(self, kwargs):
        html = _render(**kwargs)
        assert 'href="/conversations"' in html, (
            f"el vacío de {kwargs} no ofrece cómo salir del filtro"
        )

    def test_el_vacio_de_primer_arranque_no_inventa_una_accion(self):
        """No hay nada que deshacer: un botón acá sería ruido."""
        assert 'href="/conversations"' not in _render()

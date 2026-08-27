"""Carril B6 — un solo toast, una sola region aria-live, un solo nombre de evento.

El bug de fondo no era el que decia el roadmap. El servidor manda
`HX-Trigger: {"showToast": {...}}` y `{"userCreated": {...}}`, y HTMX dispara
eventos con ESE nombre, en camelCase. Un atributo HTML no puede llevar
mayusculas, asi que `@show-toast.window` escuchaba «show-toast» y **el evento
del servidor no lo tomaba nadie**: los toast de /me/password, /me/profile y el
alta de usuario nunca se veian. Lo que se veia era un `$dispatch` del cliente
puesto encima, que tapaba el sintoma con otro texto.

Borrar ese `$dispatch` —lo que pedia el roadmap— habria dejado la pantalla sin
ninguna confirmacion. Primero hay que arreglar el listener con `.camel`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_PANEL = Path(__file__).resolve().parent.parent
_TEMPLATES = _PANEL / "app" / "templates"


def _sin_comentarios_jinja(texto: str) -> str:
    return re.sub(r"\{#.*?#\}", "", texto, flags=re.DOTALL)


def _sin_comentarios_css(texto: str) -> str:
    return re.sub(r"/\*.*?\*/", "", texto, flags=re.DOTALL)


def _sin_comentarios_js(texto: str) -> str:
    texto = re.sub(r"/\*.*?\*/", "", texto, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", texto, flags=re.MULTILINE)


# Los comentarios de estos archivos nombran justamente lo que el test prohibe:
# explican por que ya no esta. Se los saca antes de mirar.
_BASE = _sin_comentarios_jinja((_TEMPLATES / "base.html").read_text(encoding="utf-8"))
_APP_JS = _sin_comentarios_js((_PANEL / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8"))
_CSS_CRUDO = (_PANEL / "app" / "static" / "css" / "custom.css").read_text(encoding="utf-8")
_CSS = _sin_comentarios_css(_CSS_CRUDO)

# Los nombres que el servidor pone adentro del HX-Trigger.
_EMITIDOS_POR_EL_SERVIDOR = {"showToast", "userCreated"}


def _eventos_del_servidor() -> set[str]:
    """Claves de primer nivel de cada HX-Trigger JSON de los routes."""
    nombres: set[str] = set()
    for path in (_PANEL / "app" / "routes").glob("*.py"):
        texto = path.read_text(encoding="utf-8")
        # json.dumps({\n    "showToast": {...}}) — la llave y la clave pueden
        # estar en renglones distintos.
        for bloque in re.findall(r'\{\s*"(\w+)"\s*:\s*\{', texto):
            nombres.add(bloque)
    return nombres


def test_el_servidor_sigue_emitiendo_los_eventos_conocidos():
    """Si cambian de nombre, el listener de abajo deja de servir."""
    assert _EMITIDOS_POR_EL_SERVIDOR <= _eventos_del_servidor()


@pytest.mark.parametrize("evento", sorted(_EMITIDOS_POR_EL_SERVIDOR))
def test_hay_un_listener_camel_para_cada_evento_del_servidor(evento):
    """Alpine convierte `@show-toast.camel` en «showToast». Sin `.camel` el
    evento del servidor cae en el vacio."""
    kebab = re.sub(r"([A-Z])", lambda m: "-" + m.group(1).lower(), evento)
    esperado = f"@{kebab}.camel.window"
    assert esperado in _BASE, (
        f"falta {esperado} en base.html: el HX-Trigger «{evento}» no lo escucha nadie"
    )


def test_una_sola_region_aria_live():
    """Habia dos y el lector de pantalla anunciaba todo dos veces."""
    assert _BASE.count("aria-live") == 1, (
        f"base.html declara {_BASE.count('aria-live')} regiones aria-live"
    )


def test_app_js_no_arma_su_propio_toast():
    assert "toast-container" not in _APP_JS
    assert "createElement('div')" not in _APP_JS
    assert "dispatchEvent(new CustomEvent('showToast'" in _APP_JS


def test_leads_no_arma_su_propio_toast():
    # El comentario que explica el cambio vive en el <script>, no en Jinja.
    leads = _sin_comentarios_js(
        _sin_comentarios_jinja((_TEMPLATES / "leads.html").read_text(encoding="utf-8"))
    )
    assert "createElementNS" not in leads
    assert "new CustomEvent('showToast'" in leads


def test_nadie_despacha_el_nombre_viejo():
    for path in sorted(_TEMPLATES.rglob("*.html")):
        contenido = _sin_comentarios_jinja(path.read_text(encoding="utf-8"))
        assert "'show-toast'" not in contenido, (
            f"{path.name} despacha «show-toast»; el nombre unico es «showToast»"
        )


def test_los_toast_del_servidor_no_estan_duplicados_en_el_cliente():
    """settings.html tenia un $dispatch encima de cada HX-Trigger del server."""
    settings = _sin_comentarios_jinja((_TEMPLATES / "settings.html").read_text(encoding="utf-8"))
    assert "$dispatch('showToast'" not in settings
    assert "$dispatch('show-toast'" not in settings


class TestColoresDelToast:
    def test_solo_dos_variantes(self):
        variantes = set(re.findall(r"\.toast-live--(\w+)", _CSS))
        assert variantes == {"neutral", "error"}, variantes

    @pytest.mark.parametrize("hex_viejo", ["#16a34a", "#d97706", "bg-green-600", "bg-red-600"])
    def test_los_colores_que_no_pasaban_no_vuelven(self, hex_viejo):
        """Blanco sobre #16a34a da 3,30:1 y sobre #d97706 da 3,19:1."""
        assert hex_viejo not in _CSS
        assert hex_viejo not in _BASE

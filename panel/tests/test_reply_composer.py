"""El composer del hilo existe una sola vez, y el doble envío no vuelve.

El bug: `conversation_thread.html` traía el form con `hx-indicator`,
`@htmx:beforeRequest` y el `@submit.prevent` que chequea `submitting`, y
`reply_response.html` lo reemplazaba entero por swap OOB **sin esos tres**. El
primer mensaje estaba protegido contra el doble envío y desde el segundo, no —
en la pantalla que le manda WhatsApp a gente real.

La misma duplicación tenía la burbuja: `reply_response.html` la escribía a mano
con `bg-indigo-600`, así que cuando el hilo pasó a burbuja clara, el mensaje
recién enviado se veía distinto de todos los demás hasta recargar.

Estos tests miran el HTML **renderizado**, no el archivo: lo que importa es lo
que llega al navegador, y así el test sobrevive a que el markup se mueva de
plantilla. El `Environment` va con `autoescape=True` porque es lo que hace
Starlette — sin eso un test puede pasar acá y fallar en producción, que es
exactamente como se escondió el bug de `img_attrs`.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

_PANEL = Path(__file__).resolve().parent.parent
_TEMPLATES = _PANEL / "app" / "templates"
_PARTIALS = _TEMPLATES / "partials"
_CSS = _PANEL / "app" / "static" / "css" / "custom.css"

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _sin_comentarios(texto: str) -> str:
    """El comentario que explica un patrón prohibido lo contiene."""
    return _HTML_COMMENT.sub("", _JINJA_COMMENT.sub("", texto))


class _Msg:
    """Lo mínimo que `render_message` le pide a un mensaje saliente."""
    id = 42
    direction = "outbound"
    sender_type = "agent"
    status = "sent"
    body = "Te confirmo la visita para el martes."
    content = None
    intent = None
    ai_model = None
    error_message = None
    error_code = None
    created_at = datetime.datetime(2026, 8, 21, 14, 35)


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=True)
    env.filters["pyt"] = lambda d, f="%H:%M": d.strftime(f) if d else ""
    env.filters["render_markdown"] = lambda s: s
    env.globals["precio"] = lambda usd=None, pyg=None, vacio="A consultar": (
        f"USD {int(usd):,}".replace(",", ".") if usd else vacio
    )
    env.globals["miles"] = lambda n: f"{int(n):,}".replace(",", ".")
    return env


def _respuesta(warning=None) -> str:
    return _env().get_template("partials/reply_response.html").render(
        msg=_Msg(), conv_id=7, warning=warning
    )


def _form_oob(html: str) -> str:
    """El `<form id="reply-form">` del HTML renderizado, con sus atributos."""
    m = re.search(r'<form id="reply-form".*?>', html, re.S)
    assert m, "la respuesta del POST no repone el composer"
    return m.group(0)


# ── el doble envío ───────────────────────────────────────────────────────────

_GUARDS = [
    ('hx-indicator="#reply-spinner"', "sin indicador, el asesor no sabe si salió"),
    ("@htmx:beforeRequest", "sin esto `submitting` nunca se pone en true"),
    ("@submit.prevent", "es el que corta el segundo submit"),
]


@pytest.mark.parametrize("atributo,por_que", _GUARDS)
def test_el_composer_repuesto_conserva_el_guard(atributo, por_que):
    """Cada uno de los tres que le faltaban a la copia. Renderizado, no grepeado."""
    assert atributo in _form_oob(_respuesta()), (
        f"el composer que repone el POST perdió {atributo}: {por_que}"
    )


def test_el_composer_repuesto_es_un_swap_oob():
    """Si deja de ser OOB, el form no se repone y el textarea queda con el texto."""
    assert 'hx-swap-oob="true"' in _form_oob(_respuesta())


def test_el_composer_del_hilo_y_el_del_post_son_el_mismo_markup():
    """La prueba de que no hay dos definiciones: salvo el atributo que los
    distingue —`hx-swap-oob`—, el form renderizado tiene que ser idéntico."""
    env = _env()
    del_post = _form_oob(_respuesta())
    del_hilo = _form_oob(
        env.from_string(
            '{% from "partials/reply_composer.html" import composer %}{{ composer(7) }}'
        ).render()
    )
    assert del_post.replace(' hx-swap-oob="true"', "") == del_hilo, (
        "los dos composers volvieron a divergir"
    )


@pytest.mark.parametrize(
    "plantilla", ["conversation_thread.html", "reply_response.html"]
)
def test_ninguna_plantilla_define_su_propio_composer(plantilla):
    """El markup vive en `reply_composer.html` y en ningún otro lado."""
    html = _sin_comentarios((_PARTIALS / plantilla).read_text(encoding="utf-8"))
    assert '<form id="reply-form"' not in html, (
        f"{plantilla} volvió a escribir el composer a mano"
    )
    assert "reply_composer.html" in html, f"{plantilla} no importa el macro"


# ── la burbuja tampoco se escribe dos veces ──────────────────────────────────

def test_la_burbuja_de_la_respuesta_sale_del_macro():
    """Se escribía a mano con `bg-indigo-600`, así que el mensaje recién enviado
    se veía distinto de todos los demás hasta recargar la página."""
    html = _respuesta()
    assert 'hx-swap-oob="beforeend:#message-list"' in html
    assert "msg-bubble__body--out" in html, "la burbuja no es la del sistema"
    assert "bg-indigo-600" not in html, "volvió la burbuja escrita a mano"


def test_la_respuesta_no_dibuja_nada_con_opacidad():
    """`ui.md`: la señal visual va con token de color, nunca con opacidad."""
    encontradas = re.findall(r"\bopacity-\d+\b", _respuesta())
    assert not encontradas, f"volvió la opacidad al composer: {encontradas}"


_HUES = (
    "indigo|purple|violet|blue|green|red|yellow|amber|orange|teal|cyan|pink|"
    "rose|emerald|lime|sky|fuchsia"
)


@pytest.mark.parametrize("warning", [None, "ventana de 24h vencida"])
def test_el_estado_del_envio_usa_el_patron_unico(warning):
    """Inventaba markup con `text-green-600` y `text-amber-600`: dos matices
    saturados más, y el noveno formato de mensaje inline del panel."""
    html = _respuesta(warning)
    assert "error-msg" in html, "el estado no usa el patrón de error_message.html"
    ofensores = re.findall(r"\b(?:bg|text|border)-(?:" + _HUES + r")-\d{2,3}\b", html)
    assert not ofensores, f"matiz saturado en la respuesta: {sorted(set(ofensores))}"


# ── el área táctil ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("selector", [".composer-send", ".composer-input"])
def test_el_composer_llega_al_area_tactil(selector):
    """El panel se usa desde el celular en visitas. El botón medía 36px."""
    css = _CSS.read_text(encoding="utf-8")
    m = re.search(r"^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.M)
    assert m, f"no existe la regla {selector}"
    assert "min-height: var(--tap)" in m.group(1), (
        f"{selector} no usa el token de área táctil"
    )

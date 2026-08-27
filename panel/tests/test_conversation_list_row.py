"""La fila de la lista de conversaciones: dos líneas, y nada de adorno.

Eran cuatro bloques apilados —nombre+hora, teléfono, preview, y una barra con
el estado del bot más «N msgs»— y la fila medía ~100px: entraban seis
conversaciones en pantalla. Ahora son dos líneas de 59px y entran nueve.

Lo que se fue, y por qué:

- **«N msgs»** no es accionable en una lista. Que una conversación tenga 4 o 40
  mensajes no cambia nada de lo que el asesor hace con ella.
- **El punto verde/rojo del bot** eran dos matices saturados más, y el rojo
  estaba puesto sobre un estado que **no es destructivo ni irreversible**, que
  es lo único para lo que `ui.md` lo reserva. Encima hablaba en los dos casos:
  ahora solo aparece cuando hay algo que decir —el bot pausado es la
  excepción— y lo dice en palabras.

Lo que **no** se fue: el teléfono. Cuando el contacto no tiene nombre, el
repositorio devuelve «Desconocido» (`conversation_repo.py:110`), así que el
teléfono es lo único que identifica la fila — y es por lo que se busca.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

_PANEL = Path(__file__).resolve().parent.parent
_TEMPLATES = _PANEL / "app" / "templates"
_LISTA = _TEMPLATES / "partials" / "conversation_list.html"

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _sin_comentarios() -> str:
    """El comentario que explica lo que se borró nombra lo que se borró."""
    return _HTML_COMMENT.sub("", _JINJA_COMMENT.sub("", _LISTA.read_text(encoding="utf-8")))


class _Conv:
    def __init__(self, id_, bot_activo):
        self.id = id_
        self.channel = "whatsapp"
        self.last_message_at = datetime.datetime(2026, 8, 21, 14, 32)
        self.is_bot_active = bot_activo
        self.message_count = 17


def _item(nombre="María Benítez", bot_activo=True, needs_reply=True, preview="Hola"):
    return {
        "conversation": _Conv(100, bot_activo),
        "contact_name": nombre,
        "contact_phone": "+595 981 234 567",
        "last_message_preview": preview,
        "needs_reply": needs_reply,
    }


def _render(**over) -> str:
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=True)
    env.filters["wa_timestamp"] = lambda d: d.strftime("%H:%M") if d else ""
    env.filters["strip_markdown"] = lambda t: t
    env.filters["tojson"] = lambda o: '""'
    return env.get_template("partials/conversation_list.html").render(
        conversations=[_item(**over)], selected_id=None, offset=0,
        limit=25, has_more=False, channel=None, q="",
    )


def _fila(html: str) -> str:
    """Solo la fila, sin los chips de canal ni el botón de cargar más."""
    m = re.search(r'<a id="conv-100".*?</a>', html, re.S)
    assert m, "no se renderizó la fila"
    return m.group(0)


# ── lo que se fue ────────────────────────────────────────────────────────────

def test_la_fila_no_muestra_el_contador_de_mensajes():
    assert "msgs" not in _fila(_render()), "volvió «N msgs», que no es accionable"


@pytest.mark.parametrize("bot_activo", [True, False])
def test_el_estado_del_bot_no_es_un_punto_de_color(bot_activo):
    """Era verde/rojo. El rojo se reserva para lo destructivo e irreversible, y
    un bot pausado no es ninguna de las dos cosas."""
    fila = _fila(_render(bot_activo=bot_activo))
    for clase in ("bg-green-500", "bg-red-500"):
        assert clase not in fila, f"volvió el punto {clase} del bot"


def test_el_bot_pausado_se_dice_en_palabras_y_solo_cuando_pasa():
    """El estado normal no necesita anunciarse; la excepción sí."""
    assert "Bot en pausa" in _fila(_render(bot_activo=False))
    assert "Bot en pausa" not in _fila(_render(bot_activo=True)), (
        "el bot andando no tiene por qué ocupar lugar en la fila"
    )


# ── lo que se queda ──────────────────────────────────────────────────────────

def test_el_telefono_sigue_en_la_fila():
    """Con el contacto sin nombre la fila dice «Desconocido»: el teléfono es lo
    único que la identifica, y es por lo que se busca."""
    fila = _fila(_render(nombre="Desconocido"))
    assert "+595 981 234 567" in fila
    assert "Desconocido" in fila


def test_el_punto_de_sin_responder_sigue_estando():
    assert "conv-dot" in _fila(_render(needs_reply=True))
    assert "conv-dot" not in _fila(_render(needs_reply=False))


def test_la_fila_entera_sigue_siendo_un_link():
    """El `<a>` estirado es lo que hace la fila tocable de punta a punta; si
    vuelve a ser un div con onclick, deja de abrirse en pestaña nueva y de
    anunciarse como link."""
    fila = _fila(_render())
    assert fila.startswith("<a "), "la fila dejó de ser un <a>"
    assert 'href="/conversations/100"' in fila


# ── la densidad ──────────────────────────────────────────────────────────────

def test_la_fila_tiene_dos_lineas_y_no_cuatro():
    """La medida real es el alto renderizado —59px, verificado en Chrome—, pero
    eso no lo puede ver un test de plantilla. Lo que sí puede fijar es la
    estructura de la que sale: dos filas flex, no cuatro bloques apilados."""
    fila = _fila(_render())
    lineas = re.findall(r'<div class="flex justify-between[^"]*"', fila)
    assert len(lineas) == 2, f"la fila tiene {len(lineas)} líneas, tiene que tener 2"


def test_el_padding_vertical_no_volvio_a_crecer():
    html = _sin_comentarios()
    m = re.search(r'class="block px-4 (py-[\d.]+)', html)
    assert m, "no encuentro el padding de la fila"
    assert m.group(1) == "py-2.5", (
        f"el padding volvió a {m.group(1)}: cada paso arriba de 2.5 se come una "
        "conversación de las nueve que entran"
    )

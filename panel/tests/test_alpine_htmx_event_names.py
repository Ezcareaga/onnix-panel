"""Los listeners de Alpine sobre eventos de htmx van en kebab-case.

**El bug que fija.** HTML baja el nombre de todo atributo a minusculas: el
parser convierte `@htmx:afterRequest.window` en `@htmx:afterrequest.window`
antes de que Alpine lo lea. htmx dispara cada evento con DOS nombres
—`htmx:afterRequest` y `htmx:after-request`— y ninguno de los dos es
`htmx:afterrequest`, asi que el handler no corre nunca.

Medido en el navegador el 2026-09-10 con htmx 2.0.4: de los seis nombres
posibles, htmx emite cuatro (camelCase y kebab de before/after) y ninguno todo
en minusculas.

Lo que costo: `conversation_thread.html` reseteaba `submitting = false` en
`@htmx:afterRequest.window` y `reply_composer.html` lo ponia en true en
`@htmx:beforeRequest`. Los dos en camelCase, o sea los dos muertos. El unico
que escribia la variable era el `@submit.prevent`, que la pone en true y no la
baja nunca: **el boton de enviar quedaba clavado en «Enviando…» para siempre**
—despues de un error Y despues de un envio exitoso—, con el textarea en
`readonly` y el submit `disabled`. El hilo quedaba mudo hasta recargar.

No se ve en una revision de codigo porque en el .html el nombre esta bien
escrito: la que lo rompe es una regla de HTML, no una falta de ortografia.

**La trampa propia de este repo** (CLAUDE.md, «Seis formas de mentir»): el
comentario que explica esta regla NOMBRA el patron que la regla prohibe. Los
comentarios de Jinja y de HTML se filtran antes de assertar, o el test se
acusa a si mismo.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

_COMENT_JINJA = re.compile(r"\{#.*?#\}", re.S)
_COMENT_HTML = re.compile(r"<!--.*?-->", re.S)

# `@htmx:loquesea` y `x-on:htmx:loquesea`, con sus modificadores.
_BINDING = re.compile(r"(?:@|x-on:)htmx:([A-Za-z-]+)((?:\.[a-z]+)*)")

# Los nombres que htmx 2.0.4 emite de verdad, medidos en el navegador. Estan
# aca para que el test explique QUE es un nombre valido y no solo que forma
# tiene que tener.
NOMBRES_QUE_HTMX_EMITE = frozenset({
    "htmx:beforeRequest", "htmx:before-request",
    "htmx:afterRequest", "htmx:after-request",
})


def _sin_comentarios(texto: str) -> str:
    return _COMENT_HTML.sub(" ", _COMENT_JINJA.sub(" ", texto))


def _plantillas() -> list[Path]:
    return sorted(_TEMPLATES.rglob("*.html"))


def _bindings() -> list[tuple[Path, int, str, str]]:
    """(archivo, linea, nombre_del_evento, modificadores) de cada binding."""
    hallazgos = []
    for ruta in _plantillas():
        limpio = _sin_comentarios(ruta.read_text(encoding="utf-8"))
        for n, linea in enumerate(limpio.splitlines(), start=1):
            for m in _BINDING.finditer(linea):
                hallazgos.append((ruta, n, m.group(1), m.group(2)))
    return hallazgos


def test_hay_bindings_para_revisar():
    """Si el regex se rompe, los asserts de abajo pasan sobre una lista vacia."""
    assert len(_bindings()) >= 5


def test_ningun_binding_de_htmx_usa_mayusculas():
    """El assert que fija el bug.

    Con `.camel` Alpine reconstruye el camelCase desde el kebab, asi que un
    binding que lo lleve queda exento — pero en este repo no hay ninguno y la
    convencion es el kebab pelado.
    """
    culpables = [
        f"{ruta.relative_to(_TEMPLATES)}:{linea}  @htmx:{evento}{mods}"
        for ruta, linea, evento, mods in _bindings()
        if any(c.isupper() for c in evento) and ".camel" not in mods
    ]
    assert not culpables, (
        "HTML baja el nombre del atributo a minusculas, asi que estos "
        "listeners escuchan un evento que htmx no emite y NO CORREN NUNCA. "
        "Va en kebab-case:\n  " + "\n  ".join(culpables)
    )


@pytest.mark.parametrize(
    "ruta,linea,evento,mods",
    _bindings(),
    ids=lambda v: v.name if isinstance(v, Path) else str(v),
)
def test_cada_binding_escucha_un_evento_que_htmx_emite(ruta, linea, evento, mods):
    """No alcanza con que sea minuscula: tiene que ser un nombre real.

    Un `@htmx:after-reqest` (con el typo) pasa el test de mayusculas y sigue
    sin correr nunca.
    """
    if ".camel" in mods:
        pytest.skip(f"{ruta.name}:{linea} usa .camel — Alpine rearma el camelCase")
    assert f"htmx:{evento}" in NOMBRES_QUE_HTMX_EMITE, (
        f"{ruta.relative_to(_TEMPLATES)}:{linea} escucha `htmx:{evento}`, que "
        f"htmx no emite. Los que emite son: {sorted(NOMBRES_QUE_HTMX_EMITE)}"
    )


def test_el_composer_prende_y_apaga_el_estado_de_enviando():
    """Las dos mitades tienen que existir, o el boton se clava.

    Es el par que fallo: si una sola de las dos vuelve a camelCase, el test de
    arriba la caza; si una DESAPARECE, la caza esta.
    """
    composer = _sin_comentarios(
        (_TEMPLATES / "partials" / "reply_composer.html").read_text(encoding="utf-8")
    )
    hilo = _sin_comentarios(
        (_TEMPLATES / "partials" / "conversation_thread.html").read_text(encoding="utf-8")
    )
    assert "@htmx:before-request" in composer, (
        "el composer no prende `submitting`: el boton nunca dice «Enviando…»"
    )
    assert "submitting = true" in composer
    assert "@htmx:after-request" in hilo, (
        "nadie apaga `submitting`: el boton queda clavado en «Enviando…» "
        "despues del primer envio, con el textarea en readonly"
    )
    assert "submitting = false" in hilo

"""El switch del bot: uno solo, y el global no se dispara de un click.

Estaba escrito **tres veces** —`wa_mode_toggle.html`, `conversation_bot_toggle.html`
y una copia inline en `conversations.html`— y ya habían divergido en lo único
que importaba: el de una conversación pedía confirmación y el global no. O sea
que el reversible y acotado confirmaba, y el que apaga el bot para TODOS los
clientes de WhatsApp se disparaba de un click.

Ahora la confirmación es del macro, no de quien lo llama: no se puede agregar
un alcance nuevo y olvidarla. Eso es lo que este archivo fija.
"""
from __future__ import annotations

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
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)

# Los tres archivos que tenían una copia del switch.
_CONSUMIDORES = [
    "conversations.html",
    "partials/wa_mode_toggle.html",
    "partials/conversation_bot_toggle.html",
]

_ALCANCES = [("global", True), ("global", False),
             ("conversacion", True), ("conversacion", False)]


def _sin_comentarios(texto: str) -> str:
    return _HTML_COMMENT.sub("", _JINJA_COMMENT.sub("", texto))


def _render(alcance: str, activo: bool) -> str:
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=True)
    return env.from_string(
        '{% from "partials/bot_toggle.html" import bot_switch %}'
        "{{ bot_switch(alcance, activo, 7) }}"
    ).render(alcance=alcance, activo=activo)


def _boton(html: str) -> str:
    m = re.search(r"<button[^>]*>", html, re.S)
    assert m, "el switch no renderiza un <button>"
    return m.group(0)


# ── la confirmación ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("alcance,activo", _ALCANCES)
def test_todo_alcance_confirma_antes_de_tocar_el_bot(alcance, activo):
    """El bug era exactamente éste: uno confirmaba y el otro no."""
    assert "hx-confirm=" in _boton(_render(alcance, activo)), (
        f"el switch {alcance} (activo={activo}) se dispara sin confirmar"
    )


@pytest.mark.parametrize("activo", [True, False])
def test_el_aviso_del_global_nombra_su_alcance(activo):
    """«¿Estás seguro?» no dice nada. Lo que hay que saber es a cuánta gente le
    cambia el comportamiento — es el switch que apaga el bot para todos los
    clientes de WhatsApp."""
    confirmar = re.search(r'hx-confirm="([^"]*)"', _boton(_render("global", activo)))
    assert confirmar, "el global perdió la confirmación"
    texto = confirmar.group(1)
    assert "TODAS" in texto, f"el aviso no nombra el alcance: {texto!r}"
    assert "WhatsApp" in texto


def test_el_aviso_de_una_conversacion_no_exagera_su_alcance():
    """Si los dos avisos dicen lo mismo, el asesor deja de leerlos."""
    texto = re.search(
        r'hx-confirm="([^"]*)"', _boton(_render("conversacion", True))
    ).group(1)
    assert "TODAS" not in texto
    assert "esta conversación" in texto


# ── una sola definición ──────────────────────────────────────────────────────

@pytest.mark.parametrize("plantilla", _CONSUMIDORES)
def test_ninguna_plantilla_escribe_el_switch_a_mano(plantilla):
    html = _sin_comentarios((_TEMPLATES / plantilla).read_text(encoding="utf-8"))
    assert "bot_toggle.html" in html, f"{plantilla} no importa el macro"
    assert not re.search(r"<button[^>]*hx-post=\"[^\"]*(?:wa-mode|bot-toggle)", html, re.S), (
        f"{plantilla} volvió a escribir el switch a mano"
    )


def test_los_dos_alcances_comparten_markup():
    """Salvo lo que los distingue —id, url, palabra, aviso— tienen que ser el
    mismo botón. Es la prueba de que no hay dos definiciones."""
    def esqueleto(html):
        b = _boton(html)
        for attr in ("hx-post", "hx-target", "hx-confirm", "aria-label", "aria-checked"):
            b = re.sub(rf'\s{attr}="[^"]*"', "", b)
        return re.sub(r"\s+", " ", b)

    assert esqueleto(_render("global", True)) == esqueleto(_render("conversacion", True))


# ── el estado se dice en palabras y sin matiz ────────────────────────────────

_HUES = (
    "indigo|purple|violet|blue|green|red|yellow|amber|orange|teal|cyan|pink|"
    "rose|emerald|lime|sky|fuchsia"
)


@pytest.mark.parametrize("alcance,activo", _ALCANCES)
def test_el_switch_no_dice_su_estado_con_matiz(alcance, activo):
    """Eran `bg-blue-600` y `bg-amber-500`, con la etiqueta en el mismo matiz:
    dos de los trece matices saturados del panel, gastados en decir algo que ya
    dicen la posición de la perilla y la palabra de al lado."""
    ofensores = re.findall(
        r"\b(?:bg|text|border)-(?:" + _HUES + r")-\d{2,3}\b", _render(alcance, activo)
    )
    assert not ofensores, f"volvió el matiz al switch: {sorted(set(ofensores))}"


@pytest.mark.parametrize("alcance,activo", _ALCANCES)
def test_el_estado_esta_escrito(alcance, activo):
    """`ui.md`: el estado se dice SIEMPRE en palabras."""
    html = _render(alcance, activo)
    esperado = {("global", True): "Auto", ("global", False): "Manual",
                ("conversacion", True): "On", ("conversacion", False): "Off"}
    assert f"Bot: {esperado[(alcance, activo)]}" in html


@pytest.mark.parametrize("alcance,activo", _ALCANCES)
def test_el_switch_se_anuncia_como_switch(alcance, activo):
    boton = _boton(_render(alcance, activo))
    assert 'role="switch"' in boton
    assert f'aria-checked="{str(activo).lower()}"' in boton
    assert "aria-label=" in boton


# ── el área táctil ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("alcance,activo", _ALCANCES)
def test_el_switch_tiene_area_tactil_de_44(alcance, activo):
    """Medía 20x36. El panel se usa desde el celular en visitas."""
    assert "tap-44" in _boton(_render(alcance, activo))


def test_la_utility_de_area_tactil_usa_el_token():
    """El carril B definió `--tap: 44px` y el patrón del `::after`; la utility
    no existía, y por eso el audit contó 105 controles chicos."""
    css = _CSS_COMMENT.sub("", _CSS.read_text(encoding="utf-8"))
    m = re.search(r"\.tap-44::after \{([^}]*)\}", css)
    assert m, "no existe la utility .tap-44"
    cuerpo = m.group(1)
    assert "var(--tap)" in cuerpo, (
        "la utility no sale del token: un 44 escrito a mano se desincroniza"
    )
    # `max(100%, var(--tap))` y no `var(--tap)` pelado. La caja fija de 44
    # servia para un icono cuadrado y le robaba ancho a todo lo demas: sobre el
    # chip de 66x24 de `/stats` plantaba 44x44 centrados y perdia 11px de cada
    # lado del area que el control ya tenia.
    for prop in ("width", "height"):
        assert f"{prop}: max(100%, var(--tap))" in cuerpo, (
            f"`{prop}` volvio a una caja fija: sobre un control ancho y bajo "
            "eso no agranda el area tactil, la achica a lo ancho"
        )
    assert re.search(r"\.tap-44 \{[^}]*position: relative", css), (
        "sin position: relative el ::after se ancla a otro elemento"
    )


# ── contraste, calculado ─────────────────────────────────────────────────────

def _srgb(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _contraste(fg: str, bg: str) -> float:
    def lum(h):
        h = h.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)
    la, lb = lum(fg), lum(bg)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _tokens() -> dict[str, str]:
    css = _CSS_COMMENT.sub("", _CSS.read_text(encoding="utf-8"))
    root = css[css.index(":root"):css.index("}", css.index(":root"))]
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", root))


@pytest.mark.parametrize(
    "nombre,selector,prop,piso",
    [
        ("la etiqueta del switch", ".bot-switch__label", "color", 4.5),
        ("el borde del switch apagado", ".bot-switch--off", "border-color", 3.0),
    ],
)
def test_la_combinacion_pasa_el_piso(nombre, selector, prop, piso):
    """Sobre `--surface`, que es el fondo de la cabecera donde vive."""
    css = _CSS_COMMENT.sub("", _CSS.read_text(encoding="utf-8"))
    m = re.search(r"\." + selector.lstrip(".").replace("--", r"--") + r"\s*\{([^}]*)\}", css)
    assert m, f"no existe la regla {selector}"
    var = re.search(rf"(?<![\w-]){prop}:\s*var\(\s*--([a-z0-9-]+)\s*\)", m.group(1))
    assert var, f"{selector} no declara {prop} con un token"
    fg = _tokens()[var.group(1)]
    ratio = _contraste(fg, _tokens()["surface"])
    assert ratio >= piso, f"{nombre}: {fg} da {ratio:.2f}:1 y el piso es {piso}:1"

"""La burbuja del hilo: contraste calculado, cero opacidad, estado en palabras.

El bug que motiva este archivo: el cuerpo del mensaje del bot era blanco sobre
`bg-blue-500` y daba 3,68:1 — abajo del piso AA, y no en un adorno sino en el
texto principal de la pantalla donde los asesores trabajan ocho horas. Todo lo
que colgaba de la fila de la hora estaba peor porque se dibujaba con `opacity`:
la hora en 2,28, el doble check en 2,59, la etiqueta "agente" en 1,69.

Estos tests **calculan** el contraste a partir de los tokens del `:root`, no lo
comparan contra un número escrito a mano. Dos números escritos a mano en la
landing decían 5,79 y 2,89 y eran 11,30 y 5,65.

Y calculan la **combinación que se usa** —el color de `.msg-bubble__meta` sobre
el fondo de `.msg-bubble__body--out`—, no el valor de un token por separado:
`.nav-active` quedó en 1,04:1 con dos tests de tokens en verde porque ninguno
miraba el par.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PANEL = Path(__file__).resolve().parent.parent
_CSS = _PANEL / "app" / "static" / "css" / "custom.css"
_MACRO = _PANEL / "app" / "templates" / "partials" / "_message_macro.html"
_HILO = _PANEL / "app" / "templates" / "partials" / "conversation_thread.html"

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _macro_sin_comentarios() -> str:
    """El comentario que explica un patrón prohibido lo contiene.

    Sin este filtro, el bloque que documenta "antes era bg-blue-500" haría
    fallar al test que prohíbe `bg-blue-500`.
    """
    return _HTML_COMMENT.sub("", _JINJA_COMMENT.sub("", _MACRO.read_text(encoding="utf-8")))


def _css_sin_comentarios() -> str:
    return _CSS_COMMENT.sub("", _CSS.read_text(encoding="utf-8"))


# ── contraste ────────────────────────────────────────────────────────────────

def _srgb(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminancia(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def _contraste(fg: str, bg: str) -> float:
    la, lb = _luminancia(fg), _luminancia(bg)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _tokens() -> dict[str, str]:
    """Los `--nombre: #HEX` del `:root` de custom.css."""
    css = _css_sin_comentarios()
    root = css[css.index(":root"):css.index("}", css.index(":root"))]
    return {n: v for n, v in re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", root)}


def _regla(selector: str) -> str:
    """El cuerpo de una regla CSS, por selector exacto."""
    css = _css_sin_comentarios()
    m = re.search(r"^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.M)
    assert m, f"no existe la regla {selector} en custom.css"
    return m.group(1)


def _prop(selector: str, prop: str) -> str:
    """El valor de una propiedad, resolviendo `var(--token)` contra el :root."""
    cuerpo = _regla(selector)
    m = re.search(rf"(?<![\w-]){re.escape(prop)}\s*:\s*([^;]+);", cuerpo)
    assert m, f"{selector} no declara {prop}"
    valor = m.group(1).strip()
    var = re.search(r"var\(\s*--([a-z0-9-]+)\s*\)", valor)
    if var:
        token = _tokens().get(var.group(1))
        assert token, f"{selector} usa --{var.group(1)}, que no está en el :root"
        return token
    assert valor.startswith("#"), f"{selector} {prop} no es token ni hex: {valor!r}"
    return valor


_FONDO_SALIENTE = ".msg-bubble__body--out"
_FONDO_ENTRANTE = ".msg-bubble__body--in"

# (qué se lee, selector del texto, propiedad, selector del fondo, piso)
_PARES = [
    ("cuerpo saliente", _FONDO_SALIENTE, "color", _FONDO_SALIENTE, 4.5),
    ("cuerpo entrante", _FONDO_ENTRANTE, "color", _FONDO_ENTRANTE, 4.5),
    ("hora sobre el saliente", ".msg-bubble__meta", "color", _FONDO_SALIENTE, 4.5),
    ("hora sobre el entrante", ".msg-bubble__meta", "color", _FONDO_ENTRANTE, 4.5),
    ("etiqueta de autor", ".msg-bubble__author", "color", _FONDO_SALIENTE, 4.5),
    ("estado entregado", ".msg-bubble__status--ok", "color", _FONDO_SALIENTE, 4.5),
    ("estado leído", ".msg-bubble__status--read", "color", _FONDO_SALIENTE, 4.5),
    ("estado fallido", ".msg-bubble__status--error", "color", _FONDO_SALIENTE, 4.5),
    ("detalle del fallo", ".msg-bubble__error", "color", _FONDO_SALIENTE, 4.5),
    ("aviso del sistema", ".msg-bubble__system", "color", ".msg-bubble__system", 4.5),
    # La etiqueta de intención vive DEBAJO de la burbuja, sobre el blanco del
    # hilo — por eso el fondo es --surface y no el de la burbuja.
    ("intención", ".msg-intent", "color", _FONDO_ENTRANTE, 4.5),
    ("intención irreversible", ".msg-intent--irreversible", "color", _FONDO_ENTRANTE, 4.5),
    # El borde es lo único que dibuja la burbuja entrante contra el blanco del
    # panel, así que es un control: piso 3:1.
    ("inicial del avatar", ".conv-avatar", "color", ".conv-avatar", 4.5),
    ("borde del entrante", _FONDO_ENTRANTE, "border", _FONDO_ENTRANTE, 3.0),
]


@pytest.mark.parametrize("nombre,sel_fg,prop,sel_bg,piso", _PARES)
def test_la_combinacion_pasa_el_piso(nombre, sel_fg, prop, sel_bg, piso):
    if prop == "border":
        cuerpo = _regla(sel_fg)
        m = re.search(r"border:\s*[^;]*?var\(\s*--([a-z0-9-]+)\s*\)", cuerpo)
        assert m, f"{sel_fg} no declara un borde con token"
        fg = _tokens()[m.group(1)]
        # El entrante es blanco sobre el blanco del panel: el borde se mide
        # contra --surface, que es lo que hay atrás.
        bg = _tokens()["surface"]
    else:
        fg = _prop(sel_fg, prop)
        bg = _prop(sel_bg, "background")

    ratio = _contraste(fg, bg)
    assert ratio >= piso, (
        f"{nombre}: {fg} sobre {bg} da {ratio:.2f}:1, y el piso es {piso}:1"
    )


def test_hay_pares_para_chequear():
    """Si la lista se vacía, los tests de arriba no protegen nada."""
    assert len(_PARES) >= 10


# ── nada de opacidad, nada de matiz saturado ─────────────────────────────────

def test_la_burbuja_no_dibuja_nada_con_opacidad():
    """`ui.md`: la señal visual va con token de color, nunca con opacidad.

    La hora, el doble check y la etiqueta "agente" se dibujaban con
    `opacity-40/50/60/70`, que convierte cualquier color en uno que nadie eligió
    — 1,69:1 en el peor caso.
    """
    encontradas = re.findall(r"\bopacity-\d+\b", _macro_sin_comentarios())
    assert not encontradas, f"la burbuja vuelve a usar opacidad: {encontradas}"


_HUES = (
    "indigo|purple|violet|blue|green|red|yellow|amber|orange|teal|cyan|pink|"
    "rose|emerald|lime|sky|fuchsia"
)


def test_la_plantilla_no_usa_matices_de_la_paleta_por_defecto():
    """Eran doce: dos del saliente (blue-500, indigo-600) y diez del mapa de
    intenciones, que mapeaba 14 tipos de mensaje a un color cada uno.

    `ui.md` admite dos matices saturados en TODA la interfaz. Este archivo solo
    tenía diez.
    """
    ofensores = re.findall(
        r"\b(?:bg|text|border)-(?:" + _HUES + r")-\d{2,3}\b", _macro_sin_comentarios()
    )
    assert not ofensores, f"volvió un matiz saturado: {sorted(set(ofensores))}"


def test_la_intencion_no_se_dice_con_color_salvo_lo_irreversible():
    """Cero color por estado, igual que el sistema de badges.

    La excepción es `opt_out`: la baja es irreversible (regla 4) y `ui.md`
    reserva el rojo exactamente para eso.
    """
    html = _macro_sin_comentarios()
    colores = re.findall(r"'color': '([^']+)'", html)
    assert colores, "desapareció el mapa de intenciones"
    irreversibles = [c for c in colores if "irreversible" in c]
    assert len(irreversibles) == 1, (
        f"el rojo se reserva para lo irreversible, y lo llevan {len(irreversibles)}"
    )
    rama_opt_out = html.split("'opt_out'", 1)[1][:600]
    assert "msg-intent--irreversible" in rama_opt_out, "el que lo lleva no es opt_out"
    assert all(c.startswith("msg-intent") for c in colores), (
        f"alguna intención se sigue diciendo con matiz: {sorted(set(colores))}"
    )


# ── el estado se dice en palabras ────────────────────────────────────────────

_ESTADOS = {
    "failed": "No se envió",
    "undelivered": "No entregado",
    "read": "Leído",
    "delivered": "Entregado",
    "sent": "Enviado",
}


@pytest.mark.parametrize("estado,palabra", sorted(_ESTADOS.items()))
def test_cada_estado_de_entrega_se_dice_en_palabras(estado, palabra):
    """`ui.md`: el estado se dice SIEMPRE en palabras.

    Antes vivían en un `title`, que en el celular no existe, y el único signo
    visible era un `✓` de texto a 2,59:1.
    """
    html = _macro_sin_comentarios()
    assert f"msg.status == '{estado}'" in html, f"desapareció la rama de {estado}"
    rama = html.split(f"msg.status == '{estado}'", 1)[1][:900]
    assert palabra in rama, f"la rama de {estado} no dice «{palabra}»"


def test_los_dos_estados_que_piden_algo_se_ven_sin_lector_de_pantalla():
    """Fallido y no entregado son los únicos que exigen acción del asesor.

    Los que salieron bien pueden llevar la palabra en `sr-only` porque el icono
    alcanza; estos dos no.
    """
    html = _macro_sin_comentarios()
    for estado, palabra in (("failed", "No se envió"), ("undelivered", "No entregado")):
        rama = html.split(f"msg.status == '{estado}'", 1)[1][:900]
        visible = rama.split(palabra)[0]
        assert "sr-only" not in visible[-120:], (
            f"«{palabra}» quedó escondida en sr-only: es el estado que pide acción"
        )


def test_el_motivo_del_fallo_sale_del_title():
    """`msg.error_message` está en la base y vivía solo en un atributo `title`."""
    html = _macro_sin_comentarios()
    assert "msg.error_message" in html
    assert "msg-bubble__error" in html


# ── lo que arrastraba la misma plantilla ─────────────────────────────────────

def test_el_precio_de_la_tarjeta_usa_el_formato_unico():
    """Era el último precio del panel con coma: `${{ "{:,.0f}".format(...) }}`.

    En Paraguay el separador de miles es el punto, y `precio` ya es global de
    Jinja desde el carril J.
    """
    html = _macro_sin_comentarios()
    assert "precio(prop.price_usd" in html, "la tarjeta no usa el global `precio`"
    assert ":,.0f" not in html, "volvió el formato con coma"


def test_el_placeholder_de_la_foto_no_es_un_emoji():
    """`ui.md`: una sola familia de iconos, nunca emojis."""
    html = _MACRO.read_text(encoding="utf-8")
    assert "🏠" not in html
    assert "Onnix" in html.split("onerror", 1)[1][:600], (
        "el placeholder perdió el monograma"
    )


# ── el avatar de la cabecera ─────────────────────────────────────────────────

def test_el_avatar_no_tiene_paleta_propia():
    """Eran 10 hex crudos elegidos por la inicial del nombre.

    No es solo deuda de tokens: **6 de los 10 no pasaban AA** con el texto
    blanco de 14px bold que llevaban encima, y uno de ellos —`#D97706`— es el
    color que `base.html` ya documenta como reprobado.

    Filtra comentarios: el que explica esto los nombra a los seis.
    """
    html = _HTML_COMMENT.sub("", _JINJA_COMMENT.sub("", _HILO.read_text(encoding="utf-8")))
    hexes = re.findall(r"#[0-9A-Fa-f]{6}\b", html)
    assert not hexes, f"volvió un hex crudo a la cabecera del hilo: {hexes}"
    assert "conv-avatar" in html, "desapareció el avatar"

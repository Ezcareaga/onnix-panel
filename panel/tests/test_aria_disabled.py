"""`aria-disabled` contra `disabled` — la regla de `ui.md`, medida.

`.claude/rules/ui.md` le dedica un bloque entero:

    ¿La condicion que apaga el control lee estado que el propio handler de ese
    control escribe? Si → `aria-disabled`. No → `disabled`.

    Excepcion dura: todo `type="submit"` se queda con `disabled`, porque
    `aria-disabled` no impide el envio del formulario.

    El estado se dice siempre en palabras. La señal visual va con token de
    color, nunca con opacidad.

Hasta el 2026-08-23 la palabra `aria-disabled` aparecia **cero veces** en el
panel contra 22 apariciones de `disabled`: la regla estaba escrita y no la
aplicaba nadie. La auditoria de los 22 esta en
`docs/audit/M9_ARIA_DISABLED_20260823.md`.

Este archivo existe porque son reglas que se reintroducen solas: el proximo
control con estado de "enviando" se va a escribir con `:disabled` porque es lo
que uno tipea sin pensar.

Que mide, y como

  1. **El control que se apaga solo va con `aria-disabled`.** No se pregunta por
     una lista de archivos: se deriva. Un elemento cae en la regla si (a) ata
     `disabled` a una expresion de Alpine, (b) alguna de las variables de esa
     expresion se **asigna** en el mismo archivo, y (c) el elemento tiene al
     menos un handler propio (`@algo` / `x-on:algo`). Ese es exactamente el daño
     que la regla ataca: apretar el control lo apaga, y el foco se cae al
     `<body>` a mitad de la interaccion.

     Cuenta la asignacion aunque sea transitiva —`@keydown.enter="submit()"` con
     `submit()` haciendo `this.loading = true`— porque el efecto sobre el foco es
     el mismo esté la asignacion en el atributo o en la funcion.

     NO cae en la regla lo que apaga otro control (un `x-model` de al lado, que
     no es una asignacion con `=`), ni el servidor, ni un permiso: ahi el control
     nunca se apaga a si mismo.

  2. **La excepcion dura.** Ningun `type="submit"` lleva `aria-disabled`.

  3. **La señal no va con opacidad**, ni en la clase del elemento ni en la regla
     CSS que lo pinta.

Las cuatro trampas del `CLAUDE.md` que este test podia pisar, y donde se
esquivan:

  * **Assert por substring** (trampa 5). `aria-disabled` **contiene** `disabled`,
    y `hx-disabled-elt` tambien. Un `"disabled" in linea` daria falso negativo en
    cada `aria-disabled` que agreguemos, que es justo lo contrario de lo que
    queremos. Por eso el archivo no mira lineas: parsea el tag, tokeniza los
    atributos y compara el **nombre exacto** (`_atributos()`).

  * **El comentario que contiene lo prohibido** (la trampa propia del repo).
    `ui.md` cita las dos palabras, `visit_row.html` explica su tratamiento en un
    `{# #}`, y `public/property.html` tiene dos comentarios —uno CSS y uno JS—
    que dicen `:disabled` para contar por que ya no esta. Se filtran comentarios
    de Jinja, de HTML, de bloque y de linea antes de mirar nada.

  * **No identificar elementos por clases de estilo** (trampa 4). Los elementos
    se identifican por su tag y sus atributos de comportamiento. La unica vez que
    se mira `class` es para buscar `opacity-*`, que es literalmente lo que la
    regla prohibe — no para saber que elemento es.

  * **No parametrizar sobre la lista que se quiere probar** (trampa 6). No hay
    lista de archivos ni de elementos: todo sale de recorrer los templates. Y
    como un recorrido que no encuentra nada es verde y no prueba nada,
    `test_hay_controles_que_revisar` pone un piso: si el parser se rompe o
    alguien mueve los templates, se pone rojo en vez de pasar vacio.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PANEL = Path(__file__).resolve().parent.parent
_TEMPLATES = _PANEL / "app" / "templates"
_CUSTOM_CSS = _PANEL / "app" / "static" / "css" / "custom.css"

# --- comentarios: se van todos, y antes que nada -----------------------------
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)     # CSS y JS
_LINE_COMMENT = re.compile(r"(?<![:/])//[^\n]*")    # JS, sin comerse https://

_SCRIPT = re.compile(r"<script\b.*?</script\s*>", re.S | re.I)
_STYLE = re.compile(r"<style\b.*?</style\s*>", re.S | re.I)

# Jinja adentro de un tag: `{% if x %}disabled{% endif %}` tiene que quedar en
# un `disabled` estatico, no en tokens partidos como `%}disabled{%`.
_JINJA_TAG = re.compile(r"\{%.*?%\}", re.S)
_JINJA_VAR = re.compile(r"\{\{.*?\}\}", re.S)

# Un tag con comillas respetadas: `x-show="selectedIds.length > 0"` lleva un `>`
# adentro de un valor, y un `<[^>]*>` pelado corta el tag ahi.
_TAG = re.compile(r"<([a-zA-Z][\w-]*)((?:\"[^\"]*\"|'[^']*'|[^>\"'])*)>", re.S)

# name, y opcionalmente ="valor" / ='valor' / valor-sin-comillas.
_ATTR = re.compile(
    r"""([@:]?[A-Za-z_][\w:.\-\[\]@$]*)      # nombre
        (?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?""",
    re.X | re.S,
)

# Identificadores de una expresion, salteando los accesos a miembro: en
# `!query.trim()` el nombre es `query`, no `trim`.
_IDENT = re.compile(r"(?<![\w.$])([A-Za-z_$][\w$]*)")

_OPACITY = re.compile(r"(?:^|[\s'\"])(?:[a-z-]+:)?opacity-\d+\b")


def _sin_comentarios(texto: str) -> str:
    """Los comentarios se van, los saltos de linea se quedan.

    Si el reemplazo comiera los `\\n`, cada `archivo:linea` de un mensaje de
    error apuntaria unas lineas mas arriba de lo real — un rojo que manda a
    leer el lugar equivocado.
    """
    def _hueco(m: re.Match[str]) -> str:
        return " " + "\n" * m.group(0).count("\n") + " "

    for rx in (_JINJA_COMMENT, _HTML_COMMENT, _BLOCK_COMMENT, _LINE_COMMENT):
        texto = rx.sub(_hueco, texto)
    return texto


class Elemento:
    """Un tag con sus atributos ya tokenizados, y de que archivo salio."""

    def __init__(self, archivo: str, linea: int, tag: str, attrs: dict[str, str]):
        self.archivo = archivo
        self.linea = linea
        self.tag = tag
        self.attrs = attrs

    def __repr__(self) -> str:  # sale en el id del parametrize
        return f"{self.archivo}:{self.linea} <{self.tag}>"

    # -- los tres atributos que importan, comparados por nombre exacto --------
    @property
    def disabled_atado(self) -> str | None:
        """El valor de `:disabled` / `x-bind:disabled`, o None."""
        for k in (":disabled", "x-bind:disabled"):
            if k in self.attrs:
                return self.attrs[k]
        return None

    @property
    def tiene_disabled_estatico(self) -> bool:
        return "disabled" in self.attrs

    @property
    def aria_disabled(self) -> str | None:
        for k in ("aria-disabled", ":aria-disabled", "x-bind:aria-disabled"):
            if k in self.attrs:
                return self.attrs[k]
        return None

    @property
    def es_submit(self) -> bool:
        return self.attrs.get("type", "").strip() == "submit"

    @property
    def tiene_handler_propio(self) -> bool:
        return any(k.startswith("@") or k.startswith("x-on:") for k in self.attrs)

    @property
    def clases(self) -> str:
        return " ".join(
            v for k, v in self.attrs.items()
            if k in ("class", ":class", "x-bind:class")
        )


def _elementos_de(ruta: Path) -> list[Elemento]:
    crudo = ruta.read_text(encoding="utf-8")
    texto = _sin_comentarios(crudo)
    # Los tags no viven adentro de <script> ni de <style>; sacarlos evita que un
    # `.lb-btn:disabled` de CSS o un `b.disabled = true` de JS pasen por control.
    texto = _STYLE.sub(lambda m: "\n" * m.group(0).count("\n"), texto)
    texto = _SCRIPT.sub(lambda m: "\n" * m.group(0).count("\n"), texto)
    texto = _JINJA_TAG.sub(lambda m: " " + "\n" * m.group(0).count("\n") + " ", texto)
    texto = _JINJA_VAR.sub(lambda m: " " + "\n" * m.group(0).count("\n") + " ", texto)

    rel = str(ruta.relative_to(_TEMPLATES))
    out: list[Elemento] = []
    for m in _TAG.finditer(texto):
        attrs: dict[str, str] = {}
        for a in _ATTR.finditer(m.group(2)):
            nombre = a.group(1)
            valor = a.group(2) or a.group(3) or a.group(4) or ""
            attrs.setdefault(nombre, valor)
        out.append(Elemento(rel, texto[: m.start()].count("\n") + 1, m.group(1), attrs))
    return out


def _asignadas_en(ruta: Path) -> set[str]:
    """Variables que el archivo **asigna** con `=`, en handlers o en scripts.

    `bulkStatus: ''` (declaracion en un x-data) y `x-model="bulkStatus"` no son
    asignaciones: escribir por `x-model` es cosa del control que lo lleva, y ese
    es el caso «lo apaga otro control» que se queda con `disabled`.
    """
    texto = _sin_comentarios(ruta.read_text(encoding="utf-8"))
    return {m.group(1) for m in re.finditer(r"\b([A-Za-z_$][\w$]*)\s*=(?![=>])", texto)}


_ARCHIVOS = sorted(_TEMPLATES.rglob("*.html"))
_ELEMENTOS: list[Elemento] = []
_ASIGNADAS: dict[str, set[str]] = {}
for _f in _ARCHIVOS:
    _rel = str(_f.relative_to(_TEMPLATES))
    _ELEMENTOS.extend(_elementos_de(_f))
    _ASIGNADAS[_rel] = _asignadas_en(_f)


def _con_estado_apagado() -> list[Elemento]:
    return [
        e for e in _ELEMENTOS
        if e.tiene_disabled_estatico or e.disabled_atado is not None
        or e.aria_disabled is not None
    ]


def _se_apaga_con_su_propio_handler(e: Elemento) -> str | None:
    """La variable que lo delata, o None si no cae en la regla."""
    expr = e.disabled_atado
    if expr is None or not e.tiene_handler_propio:
        return None
    asignadas = _ASIGNADAS.get(e.archivo, set())
    for ident in _IDENT.findall(expr):
        if ident in asignadas:
            return ident
    return None


# ---------------------------------------------------------------------------
# El piso: un recorrido vacio es verde y no prueba nada.
# ---------------------------------------------------------------------------

def test_hay_templates_que_revisar():
    assert len(_ARCHIVOS) >= 30, (
        f"solo {len(_ARCHIVOS)} templates en {_TEMPLATES}. O se movieron, o la "
        "ruta quedo mal y todos los tests de abajo pasan vacios"
    )


def test_hay_controles_que_revisar():
    """Si el parser de tags se rompe, esto se pone rojo en vez de pasar vacio.

    El piso no es un numero a dedo: la auditoria del 2026-08-23 conto 14
    elementos con estado apagado (los 22 `disabled` del grep menos comentarios,
    CSS, JS y el `hx-disabled-elt`). Se deja margen para abajo por si alguna
    pantalla se rehace, pero no para que el parser devuelva cero.
    """
    hallados = _con_estado_apagado()
    assert len(hallados) >= 10, (
        f"solo {len(hallados)} controles con estado apagado. La auditoria de M9 "
        "encontro 14; si bajaron tanto, el parser dejo de reconocer tags y los "
        "otros tests son decorativos"
    )


def test_el_panel_usa_aria_disabled():
    """La regla existia y no la aplicaba nadie: cero usos contra 22 `disabled`.

    Si esto vuelve a cero, alguien revirtio M9 entero — y los tests de abajo,
    que recorren lo que hay, no lo verian.
    """
    con_aria = [e for e in _ELEMENTOS if e.aria_disabled is not None]
    assert con_aria, (
        "ningun control usa `aria-disabled`. Es el estado en el que estaba el "
        "panel antes de M9: ui.md le dedica un bloque entero a una regla que no "
        "aplicaba nadie"
    )


# ---------------------------------------------------------------------------
# La regla
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("e", _con_estado_apagado(), ids=repr)
def test_el_control_que_se_apaga_solo_no_usa_disabled(e: Elemento):
    """`disabled` en un control que su propio handler apaga = foco al `<body>`.

    Un `<select>` que apaga a un boton de al lado NO cae acá: ese boton nunca se
    apaga a si mismo, y `disabled` es lo correcto.
    """
    if e.es_submit:
        pytest.skip("es `type=submit`: lo cubre test_ningun_submit_usa_aria_disabled")
    culpable = _se_apaga_con_su_propio_handler(e)
    assert culpable is None, (
        f"{e} ata `disabled` a `{e.disabled_atado}`, y `{culpable}` se asigna en "
        f"{e.archivo} teniendo el elemento handler propio: se apaga a si mismo. "
        "ui.md pide `aria-disabled` ahi, mas una salida temprana en el handler y "
        "la señal visual con token de color"
    )


@pytest.mark.parametrize("e", _con_estado_apagado(), ids=repr)
def test_ningun_submit_usa_aria_disabled(e: Elemento):
    """La excepcion dura: `aria-disabled` NO impide el envio del formulario.

    Convertir un submit deja un boton que parece apagado y manda igual. En
    `reply_composer.html` eso es un mensaje duplicado a gente real por WhatsApp.
    """
    if not e.es_submit:
        pytest.skip("no es `type=submit`: la excepcion dura no aplica")
    assert e.aria_disabled is None, (
        f"{e} es `type=submit` y lleva `aria-disabled`. `aria-disabled` no "
        "impide el envio: el form se manda igual. La excepcion dura de ui.md "
        "dice que todo submit se queda con `disabled`"
    )


@pytest.mark.parametrize("e", _con_estado_apagado(), ids=repr)
def test_el_estado_apagado_no_se_señala_con_opacidad(e: Elemento):
    """«La señal visual va con token de color, nunca con opacidad».

    La opacidad convierte cualquier color en uno que nadie eligio. En la galeria
    publica un `opacity: .3` sobre negro medía 1,70:1.
    """
    m = _OPACITY.search(e.clases)
    assert m is None, (
        f"{e} señala su estado apagado con `{m.group(0).strip()}`. Los tokens "
        "`--disabled-bg` y `--disabled-ink` existen en el :root de custom.css"
    )


# ---------------------------------------------------------------------------
# El lado CSS de la misma regla: la clase del elemento puede estar limpia y la
# opacidad vivir en la hoja. Asi estaba `.lb-btn:disabled { opacity: .3 }`.
# ---------------------------------------------------------------------------

def _reglas_de_apagado() -> list[tuple[str, str, str]]:
    """(archivo, selector, cuerpo) de cada regla que pinta un control apagado."""
    fuentes = [(_CUSTOM_CSS.name, _sin_comentarios(_CUSTOM_CSS.read_text(encoding="utf-8")))]
    for f in _ARCHIVOS:
        crudo = _sin_comentarios(f.read_text(encoding="utf-8"))
        for m in _STYLE.finditer(crudo):
            fuentes.append((str(f.relative_to(_TEMPLATES)), m.group(0)))

    out = []
    for archivo, css in fuentes:
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
            sel, cuerpo = m.group(1).strip(), m.group(2)
            # `:hover:not(:disabled)` pinta el estado ENCENDIDO. Sacar el
            # `:not(...)` antes de decidir, o la regla del hover del composer
            # entraria por una palabra que dice justamente lo contrario.
            sel_efectivo = re.sub(r":not\([^)]*\)", "", sel)
            if ":disabled" in sel_efectivo or "[aria-disabled" in sel_efectivo:
                out.append((archivo, " ".join(sel.split()), cuerpo))
    return out


def test_hay_reglas_de_apagado_que_revisar():
    reglas = _reglas_de_apagado()
    assert reglas, (
        "ninguna regla CSS pinta el estado apagado. `aria-disabled` no lo pinta "
        "el navegador: sin una regla, el control convertido se ve encendido"
    )


@pytest.mark.parametrize("archivo,selector,cuerpo", _reglas_de_apagado(),
                         ids=[f"{a}::{s}" for a, s, _ in _reglas_de_apagado()])
def test_la_regla_de_apagado_no_usa_opacidad(archivo, selector, cuerpo):
    assert not re.search(r"\bopacity\s*:", cuerpo), (
        f"{archivo}: `{selector}` señala el estado apagado con `opacity`. "
        "ui.md lo prohibe por nombre — va con token de color"
    )

"""El área táctil de 44px, para que no vuelva a irse.

El audit del 2026-08-20 contó 105 controles por debajo de 44x44 en 31 templates.
La medición del 2026-08-23 (`docs/audit/M2_MEDICION_20260823.md`) los volvió a
medir en Chrome a 390px agrupando por destino de click, y dieron **102 regiones**
reales. El roadmap pide, textual, «un test que recorra los templates y falle si
aparece un control interactivo sin él. Sin el test vuelve a pasar».

Lo que este archivo puede y no puede hacer, dicho de frente:

**No puede** medir un target. El tamaño real sale del navegador y el propio
roadmap lo dice: «los targets se miden, no se derivan de las clases». Un test
que adivine el alto de un `<button class="px-3 py-1.5">` estaría inventando la
medición que el documento hizo de verdad.

**Sí puede** —y es donde muerde— cerrar las cuatro formas concretas en que esto
se rompe otra vez:

1. Un control nuevo escrito con un **alto fijo** menor a 44 (`h-9`, `h-10`).
   Ahí no hay nada que adivinar: `h-10` son 40px por definición de la escala de
   Tailwind, y el test la calcula en vez de copiarla.
2. `.tap-44` puesto sobre un **nativo**, donde no hace absolutamente nada:
   `<input>`, `<select>` y `<textarea>` son elementos reemplazados y no
   renderizan `::after`. Es peor que no ponerlo, porque se lee como arreglado.
3. `.tap-44` **recortado**: por el `overflow` del propio elemento, o pisado por
   un `after:` de Tailwind sobre el mismo control.
4. La regresión pelada: alguien saca la clase de un control que ya la tenía.

Las trampas que este archivo tiene que esquivar, todas nombradas en el
`CLAUDE.md` y todas alcanzables desde acá:

- **El comentario que explica el patrón lo contiene.** Se filtran los
  comentarios de Jinja y de HTML antes de mirar nada.
- **Assert por substring.** `tap-44-nativo` **contiene** `tap-44`. Un `in`
  pelado da verdadero para los dos. Acá se parsea `class` como lista de tokens
  y se compara el token exacto.
- **Identificar por clase de estilo.** Los controles se buscan por su nombre
  accesible, su `href` o su `name` — nunca por padding ni por color.
- **Parametrizar sobre la lista que se quiere probar.** Sacar una fila de
  `CONTROLES_MEDIDOS` borraría su caso en silencio. Por eso el piso de la
  cuenta vive en `test_el_piso_de_controles_con_area_tactil_no_baja`, que es un
  número literal y no sale de la lista.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PANEL = Path(__file__).resolve().parent.parent
_TEMPLATES = _PANEL / "app" / "templates"
_CSS = _PANEL / "app" / "static" / "css" / "custom.css"

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)

# La escala de espaciado de Tailwind: `h-N` = N * 0.25rem. Con la raíz en 16px
# eso son N*4 px. El número no se copia de ningún lado: se calcula.
_REM_BASE_PX = 16
_PASO_REM = 0.25
_TAP_PX = 44

# `class="…"` y no `:class="…"` ni `x-bind:class="…"`: los de Alpine son
# condicionales de estado, no la lista estable de clases del control.
_ATTR_CLASS = re.compile(r'(?<![\w:-])class="([^"]*)"')

# Tolera `>` adentro de un valor entre comillas — los hay, en los `@click` de
# Alpine y en los `hx-vals`. Un `[^>]*` los corta al medio.
_TAG = re.compile(
    r"""<(button|a|label|input|select|textarea)\b"""
    r"""((?:[^>"']|"[^"]*"|'[^']*')*)>""",
    re.S,
)

_NATIVOS = {"input", "select", "textarea"}

# El portal público y la landing tienen su propia medición y dan cero regiones
# bajo 44x44. Este archivo es el panel.
_FUERA_DE_ALCANCE = ("public",)


def _plantillas() -> list[Path]:
    return sorted(
        p for p in _TEMPLATES.rglob("*.html")
        if not any(d in p.parts for d in _FUERA_DE_ALCANCE)
    )


def _sin_comentarios(texto: str) -> str:
    """La trampa propia del repo: el comentario que explica un patrón lo
    contiene. Los dos comentarios de arriba nombran `tap-44` y `after:`."""
    return _HTML_COMMENT.sub(" ", _JINJA_COMMENT.sub(" ", texto))


def _tags(ruta: Path):
    """(tag, texto de la etiqueta de apertura) de cada control de un template."""
    texto = _sin_comentarios(ruta.read_text(encoding="utf-8"))
    for m in _TAG.finditer(texto):
        yield m.group(1).lower(), m.group(0)


def _clases(etiqueta: str) -> set[str]:
    """Los tokens de `class`, exactos. `tap-44-nativo` contiene `tap-44`: sin
    tokenizar, cualquier `in` confunde uno con el otro."""
    tokens: set[str] = set()
    for m in _ATTR_CLASS.finditer(etiqueta):
        tokens.update(m.group(1).split())
    return tokens


def _alto_fijo_px(clases: set[str]) -> float | None:
    """El alto en px si el control lo declara fijo con `h-N`, o None.

    `h-9` no es una pista sobre el alto: **es** el alto. Por eso este caso sí
    se puede juzgar sin navegador, y es el único que se juzga.
    """
    altos = []
    for c in clases:
        m = re.fullmatch(r"h-(\d+(?:\.\d+)?)", c)
        if m:
            altos.append(float(m.group(1)) * _PASO_REM * _REM_BASE_PX)
    return min(altos) if altos else None


# ── el barrido, sobre lo que sí es derivable ─────────────────────────────────

def test_el_barrido_ve_todas_las_etiquetas_que_dice_ver():
    """Antes de creerle al barrido, medir el barrido.

    El escáner de etiquetas tolera `>` adentro de comillas. Si algún día un
    template mete uno donde el escáner no lo espera, el barrido dejaría de ver
    controles y todos los tests de abajo se pondrían verdes por vacío.
    """
    faltantes = []
    for ruta in _plantillas():
        texto = _sin_comentarios(ruta.read_text(encoding="utf-8"))
        vistos = [t for t, _ in _tags(ruta)]
        for tag in ("button", "a", "label", "input", "select", "textarea"):
            crudos = len(re.findall(rf"<{tag}\b", texto))
            if crudos != vistos.count(tag):
                faltantes.append(
                    f"  {ruta.relative_to(_TEMPLATES)}  <{tag}>: "
                    f"{crudos} en el archivo, {vistos.count(tag)} vistos"
                )
    assert not faltantes, (
        "el escaner de etiquetas se pierde controles — todo lo de abajo estaria "
        "midiendo menos de lo que dice:\n" + "\n".join(faltantes)
    )


def test_todo_control_con_alto_fijo_menor_a_44_lleva_tap_44():
    """`h-10` son 40px. No hay nada que medir en el navegador: el control
    declara su propio alto y está por debajo del piso táctil."""
    culpables = []
    for ruta in _plantillas():
        for tag, etiqueta in _tags(ruta):
            if tag in _NATIVOS:
                continue
            clases = _clases(etiqueta)
            alto = _alto_fijo_px(clases)
            if alto is not None and alto < _TAP_PX and "tap-44" not in clases:
                culpables.append(
                    f"  {ruta.relative_to(_TEMPLATES)}  <{tag}> de {alto:.0f}px "
                    f"sin `tap-44`: {' '.join(etiqueta.split())[:90]}"
                )
    assert not culpables, (
        f"controles que declaran menos de {_TAP_PX}px de alto y no estiran su "
        "area tactil:\n" + "\n".join(culpables)
    )


def test_todo_nativo_con_alto_fijo_menor_a_44_lleva_tap_44_nativo():
    """El nativo no tiene `::after`: su área táctil es la caja dibujada, y la
    única forma de subirla es el `min-height` de `.tap-44-nativo`."""
    culpables = []
    for ruta in _plantillas():
        for tag, etiqueta in _tags(ruta):
            if tag not in _NATIVOS:
                continue
            clases = _clases(etiqueta)
            alto = _alto_fijo_px(clases)
            if alto is not None and alto < _TAP_PX and "tap-44-nativo" not in clases:
                culpables.append(
                    f"  {ruta.relative_to(_TEMPLATES)}  <{tag}> de {alto:.0f}px "
                    f"sin `tap-44-nativo`: {' '.join(etiqueta.split())[:90]}"
                )
    assert not culpables, (
        f"nativos que declaran menos de {_TAP_PX}px de alto:\n" + "\n".join(culpables)
    )


# ── las tres formas de poner la clase y que no haga nada ─────────────────────

def test_ningun_nativo_lleva_tap_44():
    """`input`, `select` y `textarea` son elementos reemplazados: **no**
    renderizan `::before` ni `::after`. `.tap-44` ahí no hace nada y encima se
    lee como si lo hiciera, que es la peor de las dos cosas."""
    culpables = []
    for ruta in _plantillas():
        for tag, etiqueta in _tags(ruta):
            if tag in _NATIVOS and "tap-44" in _clases(etiqueta):
                culpables.append(
                    f"  {ruta.relative_to(_TEMPLATES)}  <{tag}>: "
                    f"{' '.join(etiqueta.split())[:90]}"
                )
    assert not culpables, (
        "`.tap-44` sobre un nativo no pinta ningun pseudo-elemento. Va "
        "`tap-44-nativo`, o un `<label class=\"tap-44\">` envolviendolo:\n"
        + "\n".join(culpables)
    )


def test_ningun_control_con_tap_44_se_recorta_a_si_mismo():
    """Un `overflow` distinto de `visible` en el propio elemento recorta su
    `::after` contra la caja que el area tactil justamente tiene que exceder."""
    recorta = re.compile(r"^overflow(-[xy])?-(hidden|clip|auto|scroll)$")
    culpables = []
    for ruta in _plantillas():
        for tag, etiqueta in _tags(ruta):
            clases = _clases(etiqueta)
            if "tap-44" not in clases:
                continue
            malas = sorted(c for c in clases if recorta.fullmatch(c))
            if malas:
                culpables.append(
                    f"  {ruta.relative_to(_TEMPLATES)}  <{tag}> {malas}: "
                    f"{' '.join(etiqueta.split())[:80]}"
                )
    assert not culpables, (
        "el area tactil sobresale de la caja dibujada; un `overflow` en el "
        "mismo elemento se la come:\n" + "\n".join(culpables)
    )


def test_ningun_control_con_tap_44_usa_ademas_el_pseudo_after():
    """`.tap-44` **es** el `::after` del control. Un `after:` de Tailwind sobre
    el mismo elemento define el mismo pseudo-elemento y gana el último: el area
    tactil desaparece sin que nada se vea distinto. `property_card.html` estira
    su link con `after:inset-0`, asi que el choque no es hipotetico."""
    culpables = []
    for ruta in _plantillas():
        for tag, etiqueta in _tags(ruta):
            clases = _clases(etiqueta)
            if "tap-44" not in clases:
                continue
            otras = sorted(c for c in clases if c.startswith("after:"))
            if otras:
                culpables.append(
                    f"  {ruta.relative_to(_TEMPLATES)}  <{tag}> {otras}"
                )
    assert not culpables, (
        "dos definiciones del mismo `::after` sobre un control:\n"
        + "\n".join(culpables)
    )


# ── las utilities, contra el token ───────────────────────────────────────────

def test_la_utility_de_nativos_sale_del_token():
    css = _CSS_COMMENT.sub(" ", _CSS.read_text(encoding="utf-8"))
    m = re.search(r"\.tap-44-nativo\s*\{([^}]*)\}", css)
    assert m, "no existe la utility .tap-44-nativo"
    assert "min-height: var(--tap)" in m.group(1), (
        "el alto minimo no sale de `--tap`: un 44 escrito a mano se "
        "desincroniza del token, que es lo que ya paso con los contrastes"
    )


def test_el_grupo_del_modo_del_bot_no_recorta_el_area_de_sus_botones():
    """El segmentado «Recepcionista / Búsqueda» redondeaba sus puntas con un
    `overflow: hidden` en el grupo, a menos de 22px de los dos botones: el
    `::after` de cada uno quedaba recortado y la utility, puesta y sin efecto.
    El recorte se fue y el redondeo lo hace `.bot-mode-seg` en el CSS."""
    ruta = _TEMPLATES / "partials" / "settings_form.html"
    texto = _sin_comentarios(ruta.read_text(encoding="utf-8"))
    m = re.search(r'<div[^>]*\bclass="([^"]*bot-mode-seg[^"]*)"', texto)
    assert m, "el grupo del modo del bot perdio la clase `bot-mode-seg`"
    clases = set(m.group(1).split())
    assert "overflow-hidden" not in clases, (
        "volvio el `overflow-hidden` al grupo: recorta el area tactil de los "
        "dos botones de adentro"
    )
    css = _CSS_COMMENT.sub(" ", _CSS.read_text(encoding="utf-8"))
    assert ".bot-mode-seg > :first-child" in css, (
        "sin la regla del CSS el grupo pierde las puntas redondeadas — "
        "`rounded-l-lg` no esta compilada en `tailwind.css`, que es un "
        "artefacto commiteado"
    )


def test_los_checkbox_de_contactos_tienen_un_label_como_target():
    """El `input[type=checkbox]` de 13x13 es el peor control del panel y el
    único que no se arregla con alto: agrandar el dibujo de 13 a 44 sería otro
    diseño. El target es el `<label>` que lo envuelve, que sí toma
    pseudo-elemento y al que el toque activa igual."""
    ruta = _TEMPLATES / "contacts.html"
    texto = _sin_comentarios(ruta.read_text(encoding="utf-8"))
    checkboxes = [m for m in _TAG.finditer(texto)
                  if m.group(1).lower() == "input" and 'type="checkbox"' in m.group(0)]
    assert len(checkboxes) == 2, (
        f"contacts.html tiene {len(checkboxes)} checkbox: el de la cabecera y "
        "el de la fila. Si aparecio otro, necesita su label"
    )
    for m in checkboxes:
        anterior = texto[:m.start()]
        etiqueta = re.search(r"<label\b[^>]*>\s*$", anterior)
        assert etiqueta, (
            f"el checkbox `{' '.join(m.group(0).split())[:70]}` no esta "
            "envuelto en un <label>: sin label el target son sus 13x13"
        )
        assert "tap-44" in set(_clases(etiqueta.group(0))), (
            "el <label> que envuelve al checkbox no lleva `tap-44`, asi que "
            "el target sigue siendo el dibujo del checkbox"
        )


# ── la regresión: los controles que M2 midió y arregló ───────────────────────

# Se identifican por nombre accesible, `href`, `title` o `name` — **nunca** por
# clase de estilo. `test_nav_semantics` identificaba ítems por su padding y
# cuando el padding subió a 44px encontró cero.
#
# Ojo con esta lista: está parametrizada, así que borrar una fila **borra su
# caso** en vez de ponerlo rojo. El que ve una eliminación es
# `test_el_piso_de_controles_con_area_tactil_no_baja`.
CONTROLES_MEDIDOS = [
    # (archivo, marca dentro de la etiqueta de apertura, clase esperada)
    ("base.html", 'aria-label="Abrir menu"', "tap-44"),
    ("base.html", 'href="/logout"', "tap-44"),
    ("partials/sidebar.html", 'aria-label="Cerrar menu"', "tap-44"),
    ("partials/conversation_thread.html",
     'aria-label="Volver a lista de conversaciones"', "tap-44"),
    ("partials/conversation_thread.html",
     'aria-label="Ver actividad de la conversación"', "tap-44"),
    ("partials/conversation_thread.html",
     'title="Ver perfil del contacto"', "tap-44"),
    ("conversations.html", 'aria-label="Nuevo mensaje de WhatsApp"', "tap-44"),
    ("partials/dashboard_stats.html", 'href="/contacts?status={{ status_key }}"',
     "tap-44"),
    ("stats.html", 'href="?days={{ d }}"', "tap-44"),
    ("stats.html", 'title="Ver el stock activo de', "tap-44"),
    ("partials/settings_form.html",
     'aria-label="Alternar bot encendido/apagado"', "tap-44"),
    ("partials/settings_form.html",
     'aria-label="Alternar auto-reply leads InfoCasas"', "tap-44"),
    ("partials/settings_form.html",
     'aria-label="Alternar envío automático de seguimiento"', "tap-44"),
    ("partials/settings_form.html",
     'aria-label="Alternar auto-reply IC reenviados"', "tap-44"),
    ("partials/settings_form.html", '"mode": "recepcionista"', "tap-44"),
    ("partials/settings_form.html", '"mode": "busqueda"', "tap-44"),
    ("leads.html", 'title="Exportar los leads a Excel"', "tap-44"),
    ("leads.html", "page={{ page - 1 }}", "tap-44"),
    ("leads.html", "page={{ page + 1 }}", "tap-44"),
    ("contacts.html", 'title="Exportar contactos filtrados a CSV"', "tap-44"),
    ("contacts.html", "page={{ page - 1 }}", "tap-44"),
    ("contacts.html", "page={{ page + 1 }}", "tap-44"),
    ("properties/index.html", 'aria-label="Quitar filtro {{ chip.label }}"', "tap-44"),
    ("properties/index.html", 'aria-label="Mostrar filtros"', "tap-44"),
    # Se identifica por el handler y no por el `aria-label`: M9 lo paso a
    # `:aria-label` dinamico —tres variantes segun `loading` y `query`— y el
    # literal viejo dejo de existir. Un handler cambia cuando cambia lo que el
    # control hace; una etiqueta cambia cuando cambia como se lee.
    ("properties/index.html", '@click="submit()"', "tap-44"),
    ("properties/index.html", 'href="/properties"', "tap-44"),
    ("properties/detail.html", "history.back()", "tap-44"),
    ("properties/partials/properties_table.html",
     'aria-label="Copiar link público"', "tap-44"),
    ("properties/partials/properties_table.html",
     'aria-label="Ver original"', "tap-44"),
    ("properties/partials/properties_table.html", "page={{ page - 1 }}", "tap-44"),
    ("properties/partials/properties_table.html", "page={{ page + 1 }}", "tap-44"),
    # Familia C — los nativos.
    ("contacts.html",
     'aria-label="Buscar contactos por nombre, teléfono o email"', "tap-44-nativo"),
    ("contacts.html", 'aria-label="Filtrar por estado"', "tap-44-nativo"),
    ("contacts.html", 'aria-label="Filtrar por fuente"', "tap-44-nativo"),
    ("contacts.html", 'aria-label="Filtrar por teléfono"', "tap-44-nativo"),
    ("leads.html", 'aria-label="Buscar por nombre o teléfono"', "tap-44-nativo"),
    ("leads.html", 'aria-label="Filtrar por fuente"', "tap-44-nativo"),
    ("properties/index.html", 'aria-label="Buscar por título o ID"', "tap-44-nativo"),
    ("login.html", 'name="email"', "tap-44-nativo"),
    ("login.html", 'name="password"', "tap-44-nativo"),
]


@pytest.mark.parametrize(
    "archivo,marca,clase", CONTROLES_MEDIDOS,
    ids=[f"{a}::{m[:44]}" for a, m, _ in CONTROLES_MEDIDOS],
)
def test_el_control_medido_conserva_su_area_tactil(archivo, marca, clase):
    ruta = _TEMPLATES / archivo
    encontrados = [e for _, e in _tags(ruta) if marca in e]
    assert encontrados, (
        f"no hay ningun control con `{marca}` en {archivo}. Si se renombro, "
        "actualizar la marca; si se borro, sacar la fila"
    )
    for etiqueta in encontrados:
        assert clase in _clases(etiqueta), (
            f"{archivo}: el control `{marca}` perdio `{clase}`\n"
            f"  {' '.join(etiqueta.split())[:110]}"
        )


# El piso, que es lo que la lista de arriba no puede ver. Los números salen de
# contar, no de `len(CONTROLES_MEDIDOS)`: una lista no puede detectar que le
# sacaron un elemento. Solo bajan cuando alguien decide que bajen, y entonces
# los baja a mano y escribe por qué — igual que el techo de matices saturados.
PISO_TAP_44 = 44
PISO_TAP_44_NATIVO = 20


def _cuenta_token(token: str) -> int:
    total = 0
    for ruta in _plantillas():
        for _, etiqueta in _tags(ruta):
            for m in _ATTR_CLASS.finditer(etiqueta):
                total += m.group(1).split().count(token)
    return total


def test_el_piso_de_controles_con_area_tactil_no_baja():
    n = _cuenta_token("tap-44")
    assert n >= PISO_TAP_44, (
        f"habia {PISO_TAP_44} controles con `tap-44` y ahora hay {n}. Un "
        "control que pierde el area tactil no se ve distinto en pantalla: "
        "solo deja de poder tocarse desde el celular"
    )


def test_el_piso_de_nativos_con_alto_minimo_no_baja():
    n = _cuenta_token("tap-44-nativo")
    assert n >= PISO_TAP_44_NATIVO, (
        f"habia {PISO_TAP_44_NATIVO} nativos con `tap-44-nativo` y ahora hay {n}"
    )

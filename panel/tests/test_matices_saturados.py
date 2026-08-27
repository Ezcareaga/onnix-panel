"""M3 — el presupuesto de matices saturados del panel, medido y con techo.

`ui.md` fija «como mucho dos matices saturados decorativos en toda la interfaz»
y hasta hoy no habia nada que lo midiera: son clases de la paleta por defecto de
Tailwind escritas directo en el template, y nada las impide. Medido el
2026-08-23 sobre `origin/dev`: 12 matices en 257 usos. **Hoy son 7 en 219**, de
los cuales green 69, red 67 y amber 41 — 177, el 81%.

Se fueron 36 usos en dos pasadas. 19 eran indigo, purple, rose, sky y teal:
cinco matices que no decian ningun estado. Los otros 17 eran ambar que en
realidad era oro — 13 superficies de seleccion escritas `amber-50` porque
`--accent-wash` no tenia utility, mas dos hover redundantes y dos bordes. Bajar los que quedan pide decisiones de color que
no son mecanicas (ver el informe de M3). Este archivo hace las dos que si lo
son:

  1. **Pone un techo.** El inventario solo puede bajar. Un matiz 13, o un uso
     258, ponen el test en rojo nombrando el archivo y la linea que lo agrego.
     Sin esto vuelve a pasar, igual que paso con el allowlist de plantillas.
  2. **Calcula el contraste** de cada combinacion fondo+texto saturada que el
     panel realmente pinta, con el hex que sale de la CSS compilada. Ningun
     numero esta escrito a mano: en este repo dos numeros a mano decian 5,79 y
     2,89 y eran 11,30 y 5,65.

Dos trampas del repo, resueltas adentro del detector:

  - **Se parsean clases como clases.** `bg-onnix-accent-dark` contiene
    `bg-onnix-accent`: buscar por substring dejaba pasar el borrado del primario.
  - **Se filtran los comentarios antes de contar**, reemplazandolos por sus
    mismos saltos de linea para que el numero de linea del hallazgo siga siendo
    el real. El comentario que explica una regla nombra lo que la regla prohibe.

El portal publico (`templates/public/`) queda afuera a proposito: tiene su
propio sistema de estilos y se atendio por otro carril. `tailwind.css` tambien:
es artefacto de build, no fuente — lo regenera el Dockerfile.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

_PANEL = Path(__file__).resolve().parent.parent
_TEMPLATES = _PANEL / "app" / "templates"
_TAILWIND_BUILD = _PANEL / "app" / "static" / "css" / "tailwind.css"

# La paleta por defecto de Tailwind, partida en neutra y saturada. Los cinco
# grises son el "resto es neutro" que ui.md permite sin limite.
_NEUTROS = ("slate", "gray", "zinc", "neutral", "stone")
_MATICES = _NEUTROS + (
    "red", "orange", "amber", "yellow", "lime", "green", "emerald", "teal",
    "cyan", "sky", "blue", "indigo", "violet", "purple", "fuchsia", "pink", "rose",
)
_SATURADOS = tuple(m for m in _MATICES if m not in _NEUTROS)

_UTILIDADES = (
    "bg", "text", "border", "ring", "fill", "stroke", "from", "to", "via",
    "divide", "outline", "shadow", "decoration", "accent", "caret", "placeholder",
)

# Una clase entera, no un pedazo de linea: variantes opcionales (`hover:`,
# `md:`, `group-hover:`), utilidad, matiz, tono, y opcionalmente `/opacidad`.
_CLASE = re.compile(
    r"^(?:[a-z0-9.\[\]#%-]+:)*"
    r"(" + "|".join(_UTILIDADES) + r")"
    r"-(" + "|".join(_MATICES) + r")"
    r"-(\d{2,3})(?:/\d{1,3})?$"
)

# Los separadores reales de un atributo HTML/Jinja: espacios, comillas, llaves
# de Jinja, parentesis de una expresion de Alpine, comas de una tupla de Jinja.
_SEPARADORES = re.compile(r"""[\s"'`{}()<>,;=]+""")


def _sin_comentarios(fuente: str) -> str:
    """Saca comentarios Jinja, HTML y CSS **conservando los saltos de linea**.

    Colapsar un comentario de 8 lineas en un espacio corre la numeracion de
    todo lo que sigue, y el hallazgo pasa a apuntar a la linea equivocada.
    """
    def blanco(m: re.Match[str]) -> str:
        return "\n" * m.group(0).count("\n")

    for patron in (r"\{#.*?#\}", r"<!--.*?-->", r"/\*.*?\*/"):
        fuente = re.sub(patron, blanco, fuente, flags=re.DOTALL)
    return fuente


def _matices_en(fuente: str) -> list[tuple[int, str, str]]:
    """(linea, matiz, clase) de cada matiz SATURADO del texto."""
    hallazgos = []
    for n, linea in enumerate(_sin_comentarios(fuente).splitlines(), 1):
        for token in _SEPARADORES.split(linea):
            m = _CLASE.match(token.lstrip("."))
            if m and m.group(2) in _SATURADOS:
                hallazgos.append((n, m.group(2), token))
    return hallazgos


def _plantillas() -> list[Path]:
    return sorted(
        p for p in _TEMPLATES.rglob("*.html")
        if "public" not in p.relative_to(_TEMPLATES).parts
    )


def _inventario() -> list[tuple[str, int, str, str]]:
    """(archivo, linea, matiz, clase) de todo el panel."""
    fuera = []
    for path in _plantillas():
        rel = str(path.relative_to(_TEMPLATES))
        for n, matiz, token in _matices_en(path.read_text(encoding="utf-8")):
            fuera.append((rel, n, matiz, token))
    return fuera


# ---------------------------------------------------------------------------
# El techo
# ---------------------------------------------------------------------------

# Medido el 2026-08-23 sobre origin/dev (df1d664): 12 matices, 257 usos. Bajado
# el mismo dia a 7 matices y 238 usos por la decision 3 de Ez — indigo, purple,
# rose, sky y teal existian para 19 usos que no decian ningun estado, y salir de
# la lista es lo que los deja afuera: `test_no_aparece_un_matiz_saturado_nuevo`
# se pone rojo si alguno vuelve. Cada numero solo puede bajar.
MATICES_VIVOS = {
    "green": 69, "red": 67, "amber": 41, "blue": 17, "orange": 11,
    "yellow": 9, "emerald": 5,
}
USOS_TOTALES = 219


def test_no_aparece_un_matiz_saturado_nuevo():
    inventario = _inventario()
    vistos = Counter(matiz for _, _, matiz, _ in inventario)
    nuevos = set(vistos) - set(MATICES_VIVOS)
    detalle = "\n".join(
        f"  {a}:{n}  {t}" for a, n, m, t in inventario if m in nuevos
    )
    assert not nuevos, (
        f"matiz saturado nuevo en el panel: {sorted(nuevos)}. "
        f"ui.md presupuesta dos y el panel ya va en {len(MATICES_VIVOS)}:\n{detalle}"
    )


@pytest.mark.parametrize("matiz,techo", sorted(MATICES_VIVOS.items()))
def test_ningun_matiz_crece(matiz, techo):
    vistos = Counter(m for _, _, m, _ in _inventario())
    assert vistos[matiz] <= techo, (
        f"{matiz} paso de {techo} a {vistos[matiz]} usos. El presupuesto de "
        "M3 solo baja: si el color hace falta, sale de green/red/amber."
    )


def test_el_total_no_crece():
    total = len(_inventario())
    assert total <= USOS_TOTALES, (
        f"{total} usos de matiz saturado contra un techo de {USOS_TOTALES}."
    )


# ---------------------------------------------------------------------------
# El detector: sin esto, un regex roto deja los tres de arriba verdes y vacios
# ---------------------------------------------------------------------------

def test_el_detector_encuentra_lo_que_dice_encontrar():
    encontrados = {t for _, _, t in _matices_en('<div class="bg-red-500">')}
    assert encontrados == {"bg-red-500"}


@pytest.mark.parametrize("clase", [
    "bg-red-500",
    "hover:bg-red-500",
    "md:hover:bg-red-500",
    "bg-red-500/50",
    "text-amber-50",
    "border-green-300",
])
def test_las_variantes_tambien_cuentan(clase):
    assert _matices_en(f'<div class="{clase}">'), f"{clase} no se conto"


@pytest.mark.parametrize("clase", [
    # La trampa del repo: bg-onnix-accent-dark CONTIENE bg-onnix-accent. Un assert por
    # substring dejaba verde el borrado del fondo primario.
    "bg-onnix-accent-dark",
    "bg-onnix-accent",
    "text-onnix-ink-400",
    "bg-gray-100",        # neutro: ui.md lo permite sin limite
    "bg-slate-500",
    "text-redwood-500",   # no es un matiz de Tailwind
    "bg-red",             # sin tono no es una clase de color
])
def test_lo_que_no_es_matiz_saturado_no_se_cuenta(clase):
    assert not _matices_en(f'<div class="{clase}">'), f"{clase} se conto de mas"


def test_el_comentario_que_nombra_un_color_no_lo_suma():
    fuente = (
        "{# antes esto era bg-purple-500 y se fue #}\n"
        "<!-- tampoco cuenta bg-teal-500 -->\n"
        '<div class="bg-red-500"></div>\n'
    )
    hallazgos = _matices_en(fuente)
    assert [(n, m) for n, m, _ in hallazgos] == [(3, "red")], (
        "el detector cuenta los colores que los comentarios NOMBRAN, o corrio "
        f"la numeracion de linea: {hallazgos}"
    )


def test_el_comentario_largo_no_corre_la_numeracion():
    fuente = "{# uno\ndos\ntres #}\n" + '<div class="bg-red-500"></div>\n'
    assert _matices_en(fuente) == [(4, "red", "bg-red-500")]


# ---------------------------------------------------------------------------
# El contraste, calculado — no copiado de ningun comentario
# ---------------------------------------------------------------------------

_ATRIBUTO = re.compile(r'(?::?class="([^"]*)")')
_ES_FONDO = re.compile(r"^bg-(?:%s)-\d{2,3}$" % "|".join(_SATURADOS))
_ES_TEXTO = re.compile(r"^text-(?:(?:%s)-\d{2,3}|white)$" % "|".join(_SATURADOS))


def _paleta_compilada() -> dict[str, tuple[int, int, int]]:
    """El hex real de cada utility, leido de la CSS que el navegador recibe.

    Tailwind emite `rgb(254 226 226/var(--tw-bg-opacity))`, y las variantes
    como `.hover\\:text-blue-800:hover{...}`.
    """
    css = _TAILWIND_BUILD.read_text(encoding="utf-8")
    regla = re.compile(
        r"\.(?:[a-z0-9-]+\\:)*"
        r"((?:bg|text|border)-[a-z0-9-]+)"
        r"(?:\\/\d+)?(?:\\?:[a-z-]+)*"
        r"\{[^}]*?(?:background-color|border-color|color):\s*rgb\((\d+)\s+(\d+)\s+(\d+)"
    )
    paleta: dict[str, tuple[int, int, int]] = {}
    for m in regla.finditer(css):
        paleta.setdefault(m.group(1), (int(m.group(2)), int(m.group(3)), int(m.group(4))))
    return paleta


def _luminancia(rgb: tuple[int, int, int]) -> float:
    def lineal(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lineal(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contraste(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _luminancia(a), _luminancia(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def test_la_formula_de_contraste_da_los_valores_conocidos():
    """Sin esto, una formula rota deja pasar cualquier combinacion."""
    assert _contraste((255, 255, 255), (0, 0, 0)) == pytest.approx(21.0, abs=0.01)
    assert _contraste((255, 255, 255), (255, 255, 255)) == pytest.approx(1.0, abs=0.001)


def _combinaciones_pintadas() -> dict[tuple[str, str], list[str]]:
    """Cada par fondo+texto saturado que el panel realmente pinta.

    Empareja **por variante**: `hover:bg-red-50` va con `hover:text-red-700` si
    existe, y con el texto base si no. Emparejar sin mirar la variante inventa
    combinaciones que nadie llega a ver.
    """
    pares: dict[tuple[str, str], list[str]] = {}
    for path in _plantillas():
        rel = str(path.relative_to(_TEMPLATES))
        for n, linea in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for valor in _ATRIBUTO.findall(linea):
                fondos: dict[str, str] = {}
                textos: dict[str, str] = {}
                for token in valor.split():
                    variante, base = token.rsplit(":", 1) if ":" in token else ("", token)
                    if _ES_FONDO.match(base):
                        fondos[variante] = base
                    elif _ES_TEXTO.match(base):
                        textos[variante] = base
                for variante, fondo in fondos.items():
                    texto = textos.get(variante, textos.get(""))
                    if texto:
                        pares.setdefault((fondo, texto), []).append(f"{rel}:{n}")
    return pares


# Vacia desde el 2026-08-23: las ocho combinaciones que no llegaban a 4,5:1
# estan arregladas. Tres se fueron con su matiz (rose en «Reenviada», purple e
# indigo en los dos contadores de pestaña, decision 3), dos se arreglaron
# eligiendo color en su propia tanda (badge «baja» 3,95 -> 5,30, banner SSE
# 2,15 -> 8,29) y las tres ultimas, con la decision 5:
#
#   user_edit_row «Guardar»   bg-green-600 + text-white     3,30 -> 7,84
#                             (bg-onnix-accent + text-onnix-black: el primario del
#                              panel es oro, y el verde ahi no decia un estado)
#   stats.html chip «Captar»  bg-orange-50 + text-orange-600  3,35 -> 6,88
#   stats.html chip, hover    bg-orange-100 + text-orange-600 3,11 -> 6,38
#
# No es un skip ni un allowlist vacio por costumbre: el assert `arregladas` de
# abajo pone el test rojo si una entrada sobra, asi que dejar el set vacio es
# lo que obliga a que ninguna vuelva sin que se note.
BAJO_AA_PENDIENTES: set[tuple[str, str]] = set()


def test_toda_combinacion_saturada_pintada_pasa_AA():
    paleta = _paleta_compilada()
    pares = _combinaciones_pintadas()

    sin_compilar = sorted(
        c for par in pares for c in par if c not in paleta
    )
    assert not sin_compilar, (
        f"clases sin color en tailwind.css: {sin_compilar}. El artefacto de "
        "build quedo viejo — hay que recompilarlo antes de confiar en el numero."
    )

    bajos, detalle = set(), []
    for (fondo, texto), sitios in sorted(pares.items()):
        ratio = _contraste(paleta[fondo], paleta[texto])
        if ratio < 4.5:
            bajos.add((fondo, texto))
            detalle.append(f"  {fondo} + {texto} = {ratio:.2f}:1  {', '.join(sorted(set(sitios)))}")

    nuevas = bajos - BAJO_AA_PENDIENTES
    arregladas = BAJO_AA_PENDIENTES - bajos
    assert not nuevas, (
        "combinaciones saturadas nuevas bajo el piso de 4,5:1 de ui.md:\n"
        + "\n".join(d for d in detalle if any(f"{f} + {t} " in d for f, t in nuevas))
    )
    assert not arregladas, (
        f"estas ya pasan AA: {sorted(arregladas)}. Sacalas de "
        "BAJO_AA_PENDIENTES para que la lista no mienta."
    )


def test_los_badges_de_estado_en_rojo_son_legibles():
    """`bg-red-100` lleva los badges mas caros del panel.

    «baja» es el opt-out irreversible de la regla 4 del CLAUDE.md, y estaba
    escrito dos veces: `text-red-600` en la tabla de contactos (3,95:1) y
    `text-red-700` en la ficha (5,30:1). El mismo badge, el mismo significado,
    uno abajo del piso de ui.md.

    Se busca por el fondo del badge, no por su geometria: identificar el
    elemento por su padding es lo que dejo a `test_nav_semantics` encontrando
    cero cuando el padding subio a 44px.
    """
    paleta = _paleta_compilada()
    pares = {
        par: sitios for par, sitios in _combinaciones_pintadas().items()
        if par[0] == "bg-red-100"
    }
    archivos = {s.split(":")[0] for sitios in pares.values() for s in sitios}
    assert {"contacts.html", "contacts_detail.html"} <= archivos, (
        "el detector no encontro el badge de baja en las dos pantallas donde "
        f"vive; encontro {sorted(archivos)}. Cambio el markup y este test dejo "
        "de mirar lo que dice mirar."
    )
    for (fondo, texto), sitios in sorted(pares.items()):
        ratio = _contraste(paleta[fondo], paleta[texto])
        assert ratio >= 4.5, (
            f"{texto} sobre {fondo} = {ratio:.2f}:1 en {sorted(set(sitios))}"
        )


def test_el_banner_de_reconexion_no_lleva_texto_blanco_sobre_ambar():
    """Se lo busca por su `id`, que es lo que el JS usa para mostrarlo."""
    fuente = (_TEMPLATES / "conversations.html").read_text(encoding="utf-8")
    bloque = re.search(r'<div\b[^>]*id="sse-reconnect-banner"[^>]*>', fuente, re.DOTALL)
    assert bloque, "no existe el banner #sse-reconnect-banner en conversations.html"
    clases = re.search(r'class="([^"]*)"', bloque.group(0))
    assert clases, "el banner perdio su atributo class"
    toks = clases.group(1).split()
    assert "text-white" not in toks, (
        "blanco sobre bg-amber-500 da 2,15:1. Va text-onnix-black, que da 8,29:1 "
        "— el mismo arreglo que test_accent_contrast.py impone para el acento."
    )
    paleta = _paleta_compilada()
    fondos = [t for t in toks if _ES_FONDO.match(t)]
    assert fondos == ["bg-amber-500"], f"el fondo del banner cambio: {fondos}"
    assert _contraste(paleta["bg-amber-500"], paleta["text-onnix-black"]) >= 4.5


# ---------------------------------------------------------------------------
# La regla y el techo tienen que contar lo mismo
# ---------------------------------------------------------------------------

def test_ui_md_cita_los_numeros_que_este_test_sostiene():
    """`ui.md` documenta el estado medido (12 matices, 257 usos) al explicar por
    qué la pregunta de los colores de estado sigue abierta.

    Si el techo de acá baja y la regla se queda con el número viejo, la regla
    pasa a mentir — y este archivo es la única fuente que se actualiza sola
    cuando alguien borra un color. El test ata las dos.
    """
    regla = (
        Path(__file__).parent.parent.parent / ".claude/rules/ui.md"
    ).read_text(encoding="utf-8")

    m = re.search(r"\*\*(\d+) usos en (\d+)\s*\n?matices\*\*|\*\*(\d+) usos en (\d+) matices\*\*", regla)
    if m is None:
        m = re.search(r"(\d+)\s+usos\s+en\s+(\d+)\s*\n?\s*matices", regla)
    assert m, "ui.md dejó de citar el inventario de matices"
    grupos = [g for g in m.groups() if g]
    usos, matices = int(grupos[0]), int(grupos[1])

    assert usos == USOS_TOTALES, (
        f"ui.md dice {usos} usos y el techo de este test es {USOS_TOTALES}"
    )
    assert matices == len(MATICES_VIVOS), (
        f"ui.md dice {matices} matices y el techo tiene {len(MATICES_VIVOS)}"
    )


def test_ui_md_declara_las_excepciones_de_canal():
    """El verde de WhatsApp y el azul de Telegram se decidieron el 2026-08-22 y
    vivían solo en ESTADO_UI.md, que es estado y no regla. Una excepción fuera
    de la regla es el primer permiso de una serie: así se llegó a 12."""
    regla = (
        Path(__file__).parent.parent.parent / ".claude/rules/ui.md"
    ).read_text(encoding="utf-8")
    for canal in ("WhatsApp", "Telegram"):
        assert canal in regla, (
            f"ui.md no declara la excepción de {canal}; si no está en la regla, "
            "no es una excepción"
        )

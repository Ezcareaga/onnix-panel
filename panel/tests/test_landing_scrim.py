"""El scrim del hero de la landing, verificado calculando, no leyendo comentarios.

El titular de la landing va sobre una fotografía. El contraste ahí no lo fija un
token: lo fija cuánta foto deja pasar el scrim de ``.hero::before``. Y la foto
cambia — es una propiedad real de la cartera y se reemplaza cuando se vende.

Por eso esto no mide contra *esta* foto. Mide contra la **peor foto posible**:
blanco puro en cada píxel. Si el texto pasa AA contra blanco puro, pasa contra
cualquier imagen que se ponga después, y nadie tiene que acordarse de recalcular
nada cuando la cambien.

Los números salen del CSS. No hay ninguna constante de contraste escrita a mano
en este archivo: si alguien aclara una alfa del scrim, la cuenta da distinto y
esto se pone rojo. Es la contracara de la regla del CLAUDE.md — «un test que
verifica contraste calcula el número, no lo copia de un comentario».

El modelo de los gradientes se validó contra el rasterizador del navegador
(canvas, 1440x900): mi cuenta dio rgb(49,49,49) y Chrome rgb(50,50,50) en el
píxel más claro, 1/255 de diferencia.
"""
from __future__ import annotations

import pathlib
import re

import pytest

CSS = pathlib.Path(__file__).resolve().parents[2] / "landing" / "assets" / "css" / "styles.css"

# Pisos de la WCAG 2.1, SC 1.4.3. Son la norma, no una medición nuestra.
AA_TEXTO_NORMAL = 4.5
AA_TEXTO_GRANDE = 3.0


# ---------------------------------------------------------------------------
# Lo que se lee del CSS
# ---------------------------------------------------------------------------

def _css() -> str:
    return CSS.read_text(encoding="utf-8")


def _regla(nombre: str) -> str:
    """El cuerpo de una regla del CSS, sin comentarios.

    Los comentarios se sacan primero: el de `.hero::before` cita las alfas en
    prosa, y sin filtrarlos el parser leería el comentario en vez del código.
    Ya pasó tres veces en este repo con tests que prohíben un patrón que su
    propio comentario contiene.
    """
    css = re.sub(r"/\*.*?\*/", "", _css(), flags=re.S)
    m = re.search(re.escape(nombre) + r"\s*\{(.*?)\}", css, flags=re.S)
    assert m, f"no encontré la regla {nombre} en {CSS.name}"
    return m.group(1)


def _token(nombre: str) -> tuple[int, int, int]:
    m = re.search(rf"--{nombre}:\s*#([0-9A-Fa-f]{{6}})\s*;", _css())
    assert m, f"no encontré el token --{nombre} en :root"
    h = m.group(1)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _gradiente(texto: str, tipo: str) -> str:
    """Los argumentos de `tipo-gradient(...)`, contando paréntesis.

    Un `(.*?)\\)` no sirve: cada `rgba(...)` tiene su propio paréntesis de
    cierre y el no-codicioso corta en el primero. Hay que balancear.
    """
    i = texto.find(tipo + "-gradient(")
    assert i != -1, f"el scrim perdió su capa {tipo}"
    j = i + len(tipo) + len("-gradient(")
    nivel = 1
    while nivel:
        assert j < len(texto), f"paréntesis sin cerrar en la capa {tipo}"
        nivel += {"(": 1, ")": -1}.get(texto[j], 0)
        j += 1
    return texto[i + len(tipo) + len("-gradient("): j - 1]


def _alfas(gradiente: str) -> list[tuple[float, float]]:
    """[(posición 0..1, alfa)] de las paradas de color de un gradiente.

    Cuidado con dos cosas que ya dieron un verde falso acá:

    1. Una parada puede no ser ``rgba()``. La última capa del scrim es
       ``var(--black)``, que es opaca: si el parser solo mira ``rgba()`` la
       pierde y cree que el gradiente termina translúcido.
    2. Las posiciones implícitas son solo las que faltan. Poner la última
       parada en 1.0 sin mirar si ya traía un ``%`` pisa el valor real — así
       este archivo llegó a calcular rgb(81,81,81) donde el navegador pinta
       rgb(50,50,50).
    """
    # cada parada: un color (rgba(...) o cualquier otro token) + un % opcional
    crudo = re.findall(
        r"(rgba\([^)]*\)|rgb\([^)]*\)|var\(--[\w-]+\)|#[0-9A-Fa-f]{3,8}|\b[a-z]+\b)"
        r"(?:\s+([\d.]+)%)?\s*(?:,|$)",
        gradiente,
    )
    # 'to', 'bottom', 'ellipse', 'at'... son sintaxis, no paradas
    palabras = {"to", "bottom", "top", "left", "right", "ellipse", "circle",
                "at", "closest", "farthest", "side", "corner", "in", "srgb"}
    paradas: list[tuple[float | None, float]] = []
    for color, pos in crudo:
        if color in palabras:
            continue
        m = re.match(r"rgba?\([^)]*?,\s*([\d.]+)\s*\)$", color)
        alfa = float(m.group(1)) if m and color.count(",") == 3 else 1.0
        paradas.append((float(pos) / 100 if pos else None, alfa))
    assert len(paradas) >= 2, f"esperaba al menos dos paradas, encontré {len(paradas)}: {gradiente!r}"

    # posiciones implícitas: primera 0, última 1, el resto repartido entre las
    # dos conocidas que la rodean (que es lo que hace CSS)
    pos = [p for p, _ in paradas]
    if pos[0] is None:
        pos[0] = 0.0
    if pos[-1] is None:
        pos[-1] = 1.0
    for i, v in enumerate(pos):
        if v is not None:
            continue
        a = max(j for j in range(i) if pos[j] is not None)
        b = min(j for j in range(i + 1, len(pos)) if pos[j] is not None)
        pos[i] = pos[a] + (pos[b] - pos[a]) * (i - a) / (b - a)
    return [(pos[i], paradas[i][1]) for i in range(len(paradas))]


def _interp(paradas: list[tuple[float, float]], t: float) -> float:
    t = min(max(t, paradas[0][0]), paradas[-1][0])
    for (p0, a0), (p1, a1) in zip(paradas, paradas[1:]):
        if p0 <= t <= p1:
            return a0 if p1 == p0 else a0 + (a1 - a0) * (t - p0) / (p1 - p0)
    return paradas[-1][1]


# ---------------------------------------------------------------------------
# La cuenta
# ---------------------------------------------------------------------------

def _luminancia(c: tuple[float, float, float]) -> float:
    def lin(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = c
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contraste(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = _luminancia(a), _luminancia(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def peor_pixel_del_scrim(ancho: int = 1440, alto: int = 900) -> tuple[float, float, float]:
    """El píxel más claro que el scrim deja salir con la foto en blanco puro.

    Barre el hero entero porque las dos capas tienen su mínimo en lugares
    distintos: el radial se aclara en las esquinas, el lineal al 40% de la
    altura. Multiplicar los dos mínimos daría una cota que no ocurre en ningún
    punto real, y de más.
    """
    cuerpo = _regla(".hero::before")
    radial = _gradiente(cuerpo, "radial")
    lineal = _gradiente(cuerpo, "linear")

    geo = re.search(r"ellipse\s+([\d.]+)%\s+([\d.]+)%\s+at\s+([\d.]+)%\s+([\d.]+)%", radial)
    assert geo, "no pude leer la geometría de la elipse del scrim"
    rx, ry, cx, cy = (float(g) / 100 for g in geo.groups())

    par_r, par_l = _alfas(radial), _alfas(lineal)
    # var(--black) al final del lineal: opaco, y es el color de todo el scrim.
    tinte = _token("black")

    peor, mejor_t = None, -1.0
    for yi in range(0, alto, 3):
        for xi in range(0, ancho, 3):
            dx = (xi - cx * ancho) / (rx * ancho)
            dy = (yi - cy * alto) / (ry * alto)
            a_r = _interp(par_r, min(1.0, (dx * dx + dy * dy) ** 0.5))
            a_l = _interp(par_l, yi / alto)
            transmitancia = (1 - a_r) * (1 - a_l)
            if transmitancia > mejor_t:
                mejor_t = transmitancia
                # foto = blanco puro (255) bajo las dos capas del mismo tinte
                peor = tuple(255 * transmitancia + t * (1 - transmitancia) for t in tinte)
    assert peor is not None
    return peor


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def peor():
    return peor_pixel_del_scrim()


def test_el_titular_pasa_aa_contra_la_peor_foto_posible(peor):
    """--white sobre el scrim, con la foto en blanco puro."""
    ratio = _contraste(_token("white"), peor)
    assert ratio >= AA_TEXTO_GRANDE, (
        f"el titular del hero queda en {ratio:.2f}:1 sobre el píxel más claro "
        f"que deja pasar el scrim (rgb{tuple(round(c) for c in peor)}). "
        f"Piso {AA_TEXTO_GRANDE}:1. Subí las alfas de .hero::before."
    )


def test_el_cuerpo_del_hero_pasa_aa_contra_la_peor_foto_posible(peor):
    """`.hero-sub` y `.hero-alt` van en --white a tamaño de cuerpo: piso 4,5."""
    ratio = _contraste(_token("white"), peor)
    assert ratio >= AA_TEXTO_NORMAL, (
        f"el cuerpo del hero queda en {ratio:.2f}:1 sobre el píxel más claro "
        f"del scrim. Piso {AA_TEXTO_NORMAL}:1."
    )


def test_el_hero_no_pone_oro_como_texto():
    """El acento **pasa** el contraste; no se usa por la regla de dos matices.

    Este test existe para que la decisión no se revierta por accidente creyendo
    que era una corrección de contraste. Si alguien quiere oro en el titular, va
    a tener que borrar esto a propósito y decir por qué.
    """
    cuerpo = _regla(".hero h1 em")
    assert "color: var(--white)" in cuerpo, ".hero h1 em dejó de ir en --white"
    assert "var(--accent)" in cuerpo and "border-bottom" in cuerpo, (
        "el acento del titular tiene que seguir siendo la regla de 2px, no el color del texto"
    )


def test_la_nav_es_legible_sin_javascript():
    """`nav.scrolled` lo pone el JS. El piso no puede depender de eso.

    Con el JS apagado la clase no llega nunca: sin `nav::before` la barra queda
    transparente sobre toda la página.
    """
    cuerpo = _regla("nav::before")
    alfa_arriba = _alfas(_gradiente(cuerpo, "linear"))[0][1]
    fondo = tuple(a * alfa_arriba + b * (1 - alfa_arriba)
                  for a, b in zip(_token("black"), (255, 255, 255)))
    ratio = _contraste(_token("gray-light"), fondo)
    assert ratio >= AA_TEXTO_NORMAL, (
        f"los links de la nav quedan en {ratio:.2f}:1 sobre el peor fondo posible "
        f"(blanco puro bajo el degradado al {alfa_arriba}). Piso {AA_TEXTO_NORMAL}:1."
    )


def test_el_menu_movil_es_mobile_first():
    """La hamburguesa se ve por defecto y se esconde solo como mejora.

    El menú de la landing es un ``<details>``: en escritorio el panel se destapa
    con ``::details-content``. Ese pseudo-elemento no existe en Chrome <131,
    Safari <18.4 ni Firefox <139.

    Si la hamburguesa arrancara en ``display: none`` y se encendiera solo bajo
    768px, esos navegadores en escritorio se quedarían **sin ninguna nav**: el
    override de ``::details-content`` no aplica, el ``<details>`` cerrado tapa
    los links, y el botón para abrirlo está apagado por ancho. Es el mismo bug
    que este carril vino a arreglar, mudado del celular al escritorio viejo.

    Por eso el orden es al revés y por eso esto se verifica: el desplegable es
    el estado base y el escritorio es la mejora, dentro de un ``@supports``.
    """
    css = re.sub(r"/\*.*?\*/", "", _css(), flags=re.S)

    base = _regla(".nav-burger")
    assert "display: inline-flex" in base, (
        ".nav-burger dejó de verse por defecto. Un navegador sin "
        "::details-content se queda sin nav en escritorio."
    )

    # el único apagado de la hamburguesa tiene que vivir dentro del @supports
    i = css.find("@supports selector(::details-content)")
    assert i != -1, "desapareció el guard @supports del menú de escritorio"
    j, nivel = css.index("{", i) + 1, 1
    while nivel:
        nivel += {"{": 1, "}": -1}.get(css[j], 0)
        j += 1
    dentro, fuera = css[i:j], css[:i] + css[j:]

    assert ".nav-burger { display: none; }" in dentro or "display: none" in dentro, (
        "el @supports dejó de apagar la hamburguesa en escritorio"
    )
    assert not re.search(r"\.nav-burger\s*\{[^}]*display:\s*none", fuera), (
        "la hamburguesa se apaga fuera del @supports: eso deja sin nav a los "
        "navegadores que no conocen ::details-content"
    )


# ---------------------------------------------------------------------------
# El hero del celular
# ---------------------------------------------------------------------------

def _bloque_media(consulta: str) -> str:
    """El cuerpo de un `@media`, contando llaves y sin comentarios."""
    css = re.sub(r"/\*.*?\*/", "", _css(), flags=re.S)
    i = css.find(consulta)
    assert i != -1, f"no encontré el bloque {consulta}"
    j, nivel = css.index("{", i) + 1, 1
    while nivel:
        assert j < len(css), f"llave sin cerrar en {consulta}"
        nivel += {"{": 1, "}": -1}.get(css[j], 0)
        j += 1
    return css[i:j]


def test_en_el_celular_el_titular_no_se_apoya_en_la_foto():
    """La foto del hero es una banda propia en el celular, no el fondo del texto.

    Medido en el navegador a 390x844 contra producción: la foto original es
    apaisada (1200x799) y `object-fit: cover` la recortaba a formato retrato,
    así que la casa quedaba fuera de cuadro; encima le caía el scrim de 0,88 y
    el primer viewport era texto blanco sobre negro liso, sin una sola
    propiedad a la vista.

    En banda la foto no lleva texto encima. Eso es lo que este test fija: si
    alguien la devuelve a `position: absolute`, el titular vuelve a apoyarse
    sobre una foto cuya luminancia no controla nadie.
    """
    movil = _bloque_media("@media (max-width: 768px)")

    m = re.search(r"\.hero-photo\s*\{(.*?)\}", movil, flags=re.S)
    assert m, "el bloque móvil dejó de tocar .hero-photo"
    assert "position: relative" in m.group(1), (
        "la foto del hero volvió a ser fondo en el celular: el titular se "
        "apoya sobre una foto recortada a retrato y el scrim se come el resto"
    )

    m = re.search(r"\.hero::before\s*\{(.*?)\}", movil, flags=re.S)
    assert m, "el bloque móvil dejó de redefinir el scrim del hero"
    assert "radial-gradient" not in m.group(1), (
        "el scrim radial —el que protege texto sobre foto— volvió al celular, "
        "donde ya no hay texto sobre la foto: solo apaga la banda"
    )

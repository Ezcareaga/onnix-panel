"""M7 y M8 del carril M: qué se anima y cómo se piden las imágenes.

**M7 — los `transition-all`.** El carril B fijó la regla —solo `transform`,
`opacity`, `color` y `background-color`, porque las cuatro son de composición y
pintado— y hasta borró `.card-hover` por animar `box-shadow`. El 2026-08-23 Ez
sumó `border-color`: es de la misma familia, cambiar el color de un borde no
altera su ancho. `border-width` sí, y por eso está en la lista de abajo. Nadie listó los
que animaban **todo**: `transition-all` incluye `width`, `height`, `top`,
`left`, `padding` y `margin`, que son layout y disparan reflow en cada cuadro.

Dos de los siete animaban layout de verdad:

- El pulgar del switch de IA iba de `left-0.5` a `left-3.5`. Ahora recorre los
  mismos 12px con `translate-x-3`.
- La barra de conversión animaba `width` — y la transición **era inerte**: el
  div se crea con su ancho final adentro del `style`, y una transición CSS
  necesita un cambio de valor para correr. Encima el parcial lo reemplaza HTMX
  entero cada 60s, así que siempre es un nodo nuevo. Se fue.

**M8 — `loading` en las imágenes.** Los tres logos están arriba del fold y no
quieren `lazy`, pero sí `decoding="async"`.

Lo que este test **no** exige: `loading="lazy"` en la tira de miniaturas de la
ficha. Que la ficha baje las hasta 15 fotos al abrirse es el precio de que el
contador de fotos sea veraz, y esa es una decisión abierta de Ez, no un olvido.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

_COMENT_JINJA = re.compile(r"\{#.*?#\}", re.S)
_COMENT_HTML = re.compile(r"<!--.*?-->", re.S)

# Layout: cada una dispara reflow. La lista es de `ui.md`, textual.
_PROPIEDADES_DE_LAYOUT = (
    "width", "height", "top", "left", "right", "bottom", "margin", "padding",
    "font-weight",
    # border-width mueve la caja, a diferencia de border-color, que ui.md
    # permite animar desde el 2026-08-23.
    "border-width",
)

_LOGO = "onnix_logo.svg"


def _plantillas() -> list[Path]:
    return sorted(_TEMPLATES.rglob("*.html"))


def _sin_comentarios(path: Path) -> str:
    """El comentario que explica por qué se fue `transition-all` lo nombra."""
    return _COMENT_HTML.sub("", _COMENT_JINJA.sub("", path.read_text(encoding="utf-8")))


def test_hay_plantillas_para_revisar():
    assert len(_plantillas()) >= 40


@pytest.mark.parametrize("path", _plantillas(), ids=lambda p: p.name)
def test_ninguna_plantilla_anima_todo(path):
    """`transition-all` es «animá lo que cambie», incluido el layout."""
    src = _sin_comentarios(path)
    usos = [
        f"  :{src[:m.start()].count(chr(10)) + 1}"
        for m in re.finditer(r"(?<![-\w])transition-all(?![-\w])", src)
    ]
    assert not usos, (
        f"{path.name} usa `transition-all`, que anima también width, height, "
        f"top, left, padding y margin. Va la lista explícita de lo que de "
        f"verdad cambia —`transition-colors`, `transition-transform` o "
        f"`transition-[a,b]`:\n" + "\n".join(usos)
    )


@pytest.mark.parametrize("path", _plantillas(), ids=lambda p: p.name)
def test_ninguna_transicion_arbitraria_incluye_layout(path):
    """La lista explícita tampoco puede colar una propiedad de layout."""
    src = _sin_comentarios(path)
    malas = []
    for m in re.finditer(r"transition-\[([^\]]+)\]", src):
        props = {p.strip() for p in m.group(1).split(",")}
        for prop in sorted(props & set(_PROPIEDADES_DE_LAYOUT)):
            malas.append(f"  :{src[:m.start()].count(chr(10)) + 1}  anima `{prop}`")
    assert not malas, f"{path.name} anima layout:\n" + "\n".join(malas)


# ---------------------------------------------------------------------------
# CSS plano. Las dos pruebas de arriba solo miran clases de Tailwind, y el
# portal publico (templates/public/*.html) no usa Tailwind: escribe CSS a mano
# adentro de un <style>. O sea que hasta el 2026-08-23 todo el CSS del portal
# quedaba fuera de la regla que ui.md dice que lo alcanza. Se descubrio
# mutando: animar `border-width` en property.html no ponia nada en rojo.
# ---------------------------------------------------------------------------

_ATAJOS_DE_TIEMPO = re.compile(r"^-?[\d.]+m?s$|^(ease|linear|step|cubic)")


def _propiedades_en_transition(valor: str) -> set[str]:
    """Las propiedades que nombra un `transition:`, sin duraciones ni curvas.

    `transition: opacity .2s, border-color 150ms ease-out` -> {opacity, border-color}
    """
    props = set()
    for segmento in valor.split(","):
        for palabra in segmento.strip().split():
            if _ATAJOS_DE_TIEMPO.match(palabra):
                continue
            props.add(palabra.rstrip(";"))
            break
    return props


@pytest.mark.parametrize("path", _plantillas(), ids=lambda p: p.name)
def test_ningun_css_plano_anima_layout(path):
    src = _sin_comentarios(path)
    malas = []
    for m in re.finditer(r"transition(?:-property)?:\s*([^;{}]+)", src):
        linea = src[:m.start()].count(chr(10)) + 1
        props = _propiedades_en_transition(m.group(1))
        if "all" in props:
            malas.append(f"  :{linea}  anima `all`, que incluye layout")
        for prop in sorted(props & set(_PROPIEDADES_DE_LAYOUT)):
            malas.append(f"  :{linea}  anima `{prop}`")
    assert not malas, (
        f"{path.name} anima layout en CSS plano:\n" + "\n".join(malas)
    )


def test_el_detector_de_css_plano_detecta():
    """Mutacion adentro del test: sin esto, agregar una propiedad a la lista de
    layout no prueba nada para el portal."""
    assert _propiedades_en_transition("border-width 0.2s") == {"border-width"}
    assert _propiedades_en_transition("opacity .2s, border-color 150ms ease-out") == {
        "opacity", "border-color",
    }
    # Lo que NO tiene que marcar: border-color es legal desde el 2026-08-23.
    assert not ({"border-color"} & set(_PROPIEDADES_DE_LAYOUT))
    # Y lo que si: la duracion no se confunde con una propiedad.
    assert _propiedades_en_transition("width 300ms ease-out") == {"width"}


def test_el_detector_de_transition_all_detecta(tmp_path):
    """Mutación adentro del test: un detector roto pasa en verde sin mirar."""
    p = tmp_path / "x.html"
    p.write_text('<div class="transition-all duration-100"></div>', encoding="utf-8")
    with pytest.raises(AssertionError):
        test_ninguna_plantilla_anima_todo(p)

    p.write_text('{# antes decía transition-all #}<div class="transition-colors"></div>',
                 encoding="utf-8")
    test_ninguna_plantilla_anima_todo(p)  # el comentario no cuenta

    p.write_text('<div class="transition-[width,opacity]"></div>', encoding="utf-8")
    with pytest.raises(AssertionError):
        test_ninguna_transicion_arbitraria_incluye_layout(p)


@pytest.mark.parametrize(
    "rel", ["base.html", "login.html", "partials/sidebar.html"],
)
def test_el_logo_se_decodifica_fuera_del_hilo_principal(rel):
    """Arriba del fold: `decoding=async` sí, `loading=lazy` no."""
    src = _sin_comentarios(_TEMPLATES / rel)
    m = re.search(r"<img\b[^>]*" + re.escape(_LOGO) + r"[^>]*>", src, re.S)
    assert m, f"{rel}: no se encontró el <img> del logo"
    tag = m.group(0)
    assert 'decoding="async"' in tag, f"{rel}: el logo no lleva decoding=async"
    assert 'loading="lazy"' not in tag, (
        f"{rel}: el logo está arriba del fold y `lazy` retrasa el pedido"
    )

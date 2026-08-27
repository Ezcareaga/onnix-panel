"""El CTA de la ficha se acomoda por el ancho de su caja, no por el del viewport.

El 2026-08-23 la ficha pública se recompuso a dos columnas en escritorio y el
bloque «Hablá con un asesor» cayó en la columna lateral, de 301px. La regla que
lo pasa a fila era `@media (min-width: 560px)` — mira el **viewport**, así que a
1152px de pantalla seguía forzando `flex-direction: row` adentro de una caja de
301.

La aritmética, medida en producción: 301 de bloque, menos 48 de padding y 16 de
gap quedan 253; el `.btn-wa` se lleva 189 y no encoge porque es
`white-space: nowrap`. Al título le quedaban **46px de ancho y 128 de alto**:
una letra por renglón.

Lo que este archivo cuida es la clase de bug, no el píxel: **un bloque cuyo
layout depende de su propio ancho no puede decidirlo con una media query.** El
píxel se verifica en el navegador, que es lo que manda `CLAUDE.md` para lo
visual, y quedó medido de 301 a 960.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_FICHA = Path(__file__).resolve().parents[1] / "app" / "templates" / "public" / "property.html"


def _css_sin_comentarios() -> str:
    """El CSS de la ficha, sin comentarios CSS ni de Jinja.

    Es la trampa propia de este repo: el comentario que explica el patrón
    prohibido lo contiene, y acá el comentario que explica el arreglo nombra
    `@media (min-width: 560px)` entero.
    """
    texto = _FICHA.read_text(encoding="utf-8")
    texto = re.sub(r"\{#.*?#\}", "", texto, flags=re.S)
    texto = re.sub(r"<!--.*?-->", "", texto, flags=re.S)
    return re.sub(r"/\*.*?\*/", "", texto, flags=re.S)


def _bloques_de_regla(css: str, selector: str) -> list[str]:
    """Los cuerpos `{...}` de cada aparición de `selector` como regla."""
    cuerpos = []
    for m in re.finditer(re.escape(selector) + r"\s*\{", css):
        i = m.end()
        prof = 1
        while i < len(css) and prof:
            prof += (css[i] == "{") - (css[i] == "}")
            i += 1
        cuerpos.append(css[m.end():i - 1])
    return cuerpos


def test_el_cta_declara_su_contenedor():
    """Sin `container-type` el `@container` de abajo no matchea nada y el
    bloque se queda en columna para siempre — verde y roto."""
    css = _css_sin_comentarios()
    cuerpos = _bloques_de_regla(css, "section.contacto")
    assert cuerpos, "no existe la regla `section.contacto`"
    assert any("container-type" in c for c in cuerpos), (
        "`section.contacto` no declara `container-type`: el `@container` que "
        "decide la dirección del CTA no tiene contra qué medir"
    )


def test_la_direccion_del_cta_sale_de_un_container_query():
    css = _css_sin_comentarios()
    # el `@container` tiene que existir y tocar .cta-block
    contenedores = re.findall(r"@container[^{]*\{(.*?\n\s{8}\})", css, flags=re.S)
    assert any(".cta-block" in c and "flex-direction" in c for c in contenedores), (
        "ningún `@container` fija la `flex-direction` de `.cta-block`"
    )


def test_ninguna_media_query_le_fija_la_direccion_al_cta():
    """El bug exacto: decidir por el viewport un layout que depende de la caja."""
    css = _css_sin_comentarios()
    culpables = []
    for m in re.finditer(r"@media([^{]*)\{", css):
        i = m.end()
        prof = 1
        while i < len(css) and prof:
            prof += (css[i] == "{") - (css[i] == "}")
            i += 1
        cuerpo = css[m.end():i - 1]
        for regla in _bloques_de_regla(cuerpo, ".cta-block"):
            if "flex-direction" in regla:
                culpables.append(f"@media{m.group(1).strip()}")
    assert not culpables, (
        "una media query le fija `flex-direction` a `.cta-block`: "
        f"{culpables}. El viewport no sabe cuánto mide la columna donde vive "
        "el bloque — a 1152px de pantalla la caja mide 301"
    )


def test_el_boton_no_encoge_y_por_eso_el_umbral_no_puede_bajar():
    """El piso del umbral sale de que `.btn-wa` no cede ancho.

    Si algún día el botón dejara de ser `nowrap`, el bloque podría entrar en
    fila mucho antes y este umbral pasaría a ser conservador de más. El test no
    lo prohíbe: lo deja escrito para que quien lo cambie sepa qué recalcular.
    """
    css = _css_sin_comentarios()
    reglas = _bloques_de_regla(css, ".btn-wa")
    assert reglas, "no existe la regla `.btn-wa`"
    assert any("white-space" in r and "nowrap" in r for r in reglas), (
        "`.btn-wa` dejó de ser `nowrap`. El umbral de 35rem se calculó con el "
        "botón midiendo 189px fijos: recalcularlo midiendo en el navegador"
    )

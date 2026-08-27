"""El acento de Onnix es el negro de marca, y por eso el texto encima va blanco.

Este archivo reemplaza al `test_gold_contrast.py` del panel del que se forkeo,
que imponia la regla contraria: ahi el acento era oro (#C8A951) y el texto
encima tenia que ser `text-onnix-black`, porque el blanco daba 2,27:1.

Con `--accent: #16181A` la direccion se da vuelta y el error cambia de forma:
un `bg-onnix-accent` con `text-onnix-black` encima no es texto flojo, es texto
**invisible** — 1,00:1, el mismo color contra si mismo.

Las tres variantes del acento, verificadas con la formula de WCAG 2.1:

    fondo                blanco   onnix-black
    onnix-accent          17,80      1,00
    onnix-accent-dark     21,00      1,24
    onnix-accent-light    10,93      1,63

Ninguna pasa con `text-onnix-black`. Por eso el test no admite excepciones.

Y la simetrica: el acento como TEXTO solo sirve sobre claro (17,80:1 sobre
--surface). Sobre el shell oscuro da 1,00:1, asi que ahi va `text-white`.
"""
from __future__ import annotations

import re
from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

# class="..." y :class="..." de Alpine, que pinta las mismas clases.
_ATTR = re.compile(r'(?::?class=")([^"]*)"')
# El `(?![-\w/])` final no es cosmetico: `bg-onnix-accent/10` es el acento al
# 10% de alfa, o sea una superficie casi blanca, y ahi la tinta oscura es la
# correcta. Sin el, el test prohibia el patron que quiere.
_ACENTO = re.compile(
    r"(?<![-\w])(?:hover:|focus:|active:|group-hover:)?"
    r"bg-onnix-accent(?:-dark|-light)?(?![-\w/])"
)
_TINTA_OSCURA = re.compile(
    r"(?<![-\w])(?:hover:|focus:|active:|group-hover:)?"
    r"text-onnix-(?:black|ink-900|accent|accent-ink)(?![-\w])"
)


def _infracciones() -> list[tuple[str, int, str]]:
    fuera = []
    for path in sorted(_TEMPLATES.rglob("*.html")):
        for n, linea in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for valor in _ATTR.findall(linea):
                if _ACENTO.search(valor) and _TINTA_OSCURA.search(valor):
                    fuera.append((str(path.relative_to(_TEMPLATES)), n, valor))
    return fuera


def test_ningun_fondo_de_acento_lleva_tinta_oscura():
    fuera = _infracciones()
    detalle = "\n".join(f"  {f}:{n}  {v[:90]}" for f, n, v in fuera)
    assert not fuera, (
        f"{len(fuera)} lugares pintan tinta oscura sobre el acento negro "
        f"(1,00:1 — invisible). Va text-white, que da 17,80:1:\n{detalle}"
    )


def test_el_detector_encuentra_el_patron_cuando_existe():
    """Sin esto, un regex roto deja el test de arriba pasando vacio."""
    assert _ACENTO.search("bg-onnix-accent text-onnix-black")
    assert _TINTA_OSCURA.search("bg-onnix-accent text-onnix-black")
    assert _TINTA_OSCURA.search("hover:text-onnix-ink-900")
    # -wash es una superficie clara, no el acento: ahi la tinta oscura es correcta.
    assert not _ACENTO.search("bg-onnix-accent-wash")
    assert not _ACENTO.search("bg-onnix-accent/10")
    assert not _TINTA_OSCURA.search("text-onnix-ink-600")
    assert _ACENTO_TEXTO.search("hover:text-onnix-accent-ink")
    assert not _ACENTO_TEXTO.search("text-onnix-black")


# ---------------------------------------------------------------------------
# El acento como TEXTO — el mismo 1,00:1 en la otra direccion
# ---------------------------------------------------------------------------

# Las dos superficies con fondo #16181A. Ahi el acento como texto es invisible.
_SUPERFICIES_OSCURAS = {"partials/sidebar.html", "login.html", "base.html"}

# Solo el acento POR NOMBRE. `text-onnix-black` queda fuera a proposito: el
# avatar de la barra lateral lo usa en hover contra un `bg-white` que vive en
# el <span> padre, o sea en OTRA linea, y un regex por linea no puede decidir
# esa combinacion. Prohibirlo aca seria assertar sobre lo accidental — la
# cuarta forma de mentir. Lo que si se puede probar es que nadie pida el acento
# como tinta en una superficie que el sistema declara oscura.
_ACENTO_TEXTO = re.compile(
    r"(?<![-\w])(?:hover:|group-hover:|focus:|active:)?"
    r"text-onnix-(?:accent|accent-ink)(?![-\w])"
)


def test_el_acento_no_es_texto_sobre_el_shell_oscuro():
    """#16181A sobre #16181A da 1,00:1. Sobre el shell va --shell-ink o blanco."""
    fuera = []
    for rel in sorted(_SUPERFICIES_OSCURAS):
        path = _TEMPLATES / rel
        if not path.exists():
            continue
        for n, linea in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for valor in _ATTR.findall(linea):
                if _ACENTO_TEXTO.search(valor):
                    fuera.append((rel, n, valor))
    detalle = "\n".join(f"  {f}:{n}  {v[:90]}" for f, n, v in fuera)
    assert not fuera, (
        f"{len(fuera)} lugares usan tinta de acento sobre el shell oscuro "
        f"(1,00:1). Va text-white:\n{detalle}"
    )


def test_hay_plantillas_oscuras_para_chequear():
    """Sin esto, un rename de archivo deja el test de arriba pasando vacio."""
    presentes = [r for r in _SUPERFICIES_OSCURAS if (_TEMPLATES / r).exists()]
    assert len(presentes) == 3, f"faltan superficies oscuras: {presentes}"

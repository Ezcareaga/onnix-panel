"""El sello de versión de los assets de la landing.

nginx sirve `/landing-assets/` con `expires` de 30 días y el `?v=` del HTML
estaba escrito a mano. El 2026-08-20 el hero del celular se desplegó y el
navegador siguió pintando el CSS anterior: el cambio estaba en el servidor y no
llegaba a nadie que ya hubiera entrado al sitio.

Lo que se verifica acá es lo que puede romper de verdad: que el reemplazo toque
el número y **no la ruta**. Un regex que se coma el `href` deja la landing sin
estilos, que es peor que un CSS viejo.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "stamp_landing_assets", REPO / "scripts" / "stamp_landing_assets.py"
)
sello = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sello)

HTML = (
    '<link rel="stylesheet" href="/landing-assets/css/styles.css?v=6">\n'
    '<script src="/landing-assets/js/main.js?v=3" defer></script>\n'
)


def test_sella_css_y_js_conservando_la_ruta():
    nuevo, n = sello.sellar(HTML, "a1b2c3d")

    assert n == 2
    assert 'href="/landing-assets/css/styles.css?v=a1b2c3d"' in nuevo
    assert 'src="/landing-assets/js/main.js?v=a1b2c3d"' in nuevo


def test_es_idempotente():
    una, _ = sello.sellar(HTML, "a1b2c3d")
    dos, _ = sello.sellar(una, "a1b2c3d")

    assert una == dos


def test_no_toca_lo_que_no_es_un_asset_de_la_landing():
    """El og:image y los links externos llevan el mismo prefijo de dominio."""
    otro = (
        '<meta property="og:image" content="https://onnix.com.py/landing-assets/hero.webp">\n'
        '<a href="/propiedades?v=6">Ver</a>\n'
        '<img src="/images/remax/123/1.webp">\n'
    )

    nuevo, n = sello.sellar(otro, "a1b2c3d")

    assert n == 0
    assert nuevo == otro


def test_la_landing_del_repo_tiene_assets_con_version():
    """Si alguien saca el `?v=`, el sello del deploy deja de tener qué sellar."""
    html = (REPO / "landing" / "index.html").read_text(encoding="utf-8")

    _, n = sello.sellar(html, "pytest")

    assert n >= 2, (
        "la landing dejó de pedir sus assets con ?v=: sin eso el deploy no "
        "puede invalidar la caché de 30 días que pone nginx"
    )

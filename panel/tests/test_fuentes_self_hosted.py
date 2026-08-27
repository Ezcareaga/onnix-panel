"""Carril K — una sola familia, servida desde el propio dominio.

El shell pedia 13 variantes de 3 familias (Inter, Plus Jakarta Sans y
Cormorant Garamond) a fonts.bunny.net: dos handshakes contra un tercero
bloqueando el render, y una tipografia que ademas no era la que aprobo
DESIGN_DIRECTION.

Queda Outfit, variable, en dos archivos que cubren todo el rango de peso.
Este test cuida las cuatro cosas que se rompen en silencio: que no vuelva un
`<link>` a un CDN de fuentes, que los archivos existan, que las @font-face los
apunten, y que las tres declaraciones de familia (tailwind.config, input.css y
custom.css) no vuelvan a divergir.
"""
from __future__ import annotations

import re
from pathlib import Path

_PANEL = Path(__file__).resolve().parent.parent
_TEMPLATES = _PANEL / "app" / "templates"
_STATIC = _PANEL / "app" / "static"
_CUSTOM = (_STATIC / "css" / "custom.css").read_text(encoding="utf-8")
_CONFIG = (_PANEL / "tailwind.config.js").read_text(encoding="utf-8")
_INPUT = (_STATIC / "css" / "input.css").read_text(encoding="utf-8")

# Los comentarios nombran las familias que estos tests prohiben: sin sacarlos,
# el test falla contra su propia documentacion.
_CONFIG_CODIGO = re.sub(r"//[^\n]*", "", _CONFIG)
_INPUT_CODIGO = re.sub(r"/\*.*?\*/", "", _INPUT, flags=re.DOTALL)
_MAIN = (_PANEL / "app" / "main.py").read_text(encoding="utf-8")

# Los tres documentos con <head> propio. El portal (templates/public/*.html)
# todavia pide a Google y se migra en el carril J.
_DOCUMENTOS = ["base.html", "login.html", "error.html"]

_ARCHIVOS = ["outfit-latin.woff2", "outfit-latin-ext.woff2"]


def _codigo(rel: str) -> str:
    """Sin los comentarios Jinja, que citan el CDN que este test prohibe."""
    html = (_TEMPLATES / rel).read_text(encoding="utf-8")
    return re.sub(r"\{#.*?#\}", "", html, flags=re.DOTALL)


def test_ningun_documento_pide_fuentes_a_un_tercero():
    for rel in _DOCUMENTOS:
        codigo = _codigo(rel)
        for cdn in ("fonts.bunny.net", "fonts.googleapis.com", "fonts.gstatic.com"):
            assert cdn not in codigo, f"{rel} volvio a pedirle fuentes a {cdn}"


def test_los_archivos_estan_en_el_repo():
    for nombre in _ARCHIVOS:
        f = _STATIC / "fonts" / nombre
        assert f.exists(), f"falta {nombre}"
        assert f.read_bytes()[:4] == b"wOF2", f"{nombre} no es woff2"


def test_las_font_face_apuntan_a_los_archivos_del_repo():
    for nombre in _ARCHIVOS:
        assert f"/static/fonts/{nombre}" in _CUSTOM
    assert _CUSTOM.count("@font-face") == len(_ARCHIVOS)


def test_la_font_face_cubre_todo_el_rango_de_peso():
    """Outfit es variable: un archivo da 400, 600 y 700. Declarar un peso
    unico obliga al navegador a sintetizar el resto."""
    assert _CUSTOM.count("font-weight: 100 900;") == len(_ARCHIVOS)


def test_las_fuentes_no_bloquean_el_render():
    assert _CUSTOM.count("font-display: swap;") == len(_ARCHIVOS)
    for rel in _DOCUMENTOS:
        assert 'rel="preload"' in _codigo(rel), f"{rel} no precarga el subset latin"


def test_una_sola_familia_declarada_en_los_tres_lugares():
    """tailwind.config, input.css y custom.css se desincronizaron una vez con
    los colores. Con la tipografia el sintoma seria el mismo: dos familias
    distintas segun quien pinte."""
    for muerta in ("Inter", "Plus Jakarta Sans", "Cormorant"):
        assert muerta not in _CONFIG_CODIGO, f"{muerta} sigue en tailwind.config.js"
        assert muerta not in _INPUT_CODIGO, f"{muerta} sigue en input.css"
    assert _CONFIG_CODIGO.count("'Outfit'") == 2, "sans y display, las dos a Outfit"
    assert "'luxe'" not in _CONFIG_CODIGO


def test_ninguna_plantilla_usa_la_familia_que_se_fue():
    for f in _TEMPLATES.rglob("*.html"):
        assert "font-luxe" not in f.read_text(encoding="utf-8"), f.name


def test_el_csp_dejo_de_permitir_el_cdn_que_ya_nadie_usa():
    csp = _MAIN[_MAIN.index("_CSP_HEADER_VALUE"):]
    csp = csp[:csp.index(")\n")]
    assert "bunny.net;" not in csp
    assert "'self'" in csp

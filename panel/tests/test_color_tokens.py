"""Carril B1 — el color del panel sale del :root, no de hex sueltos.

Habia dos paletas paralelas: el :root de custom.css (que no existia) y los
colores de tailwind.config.js, donde onnix-black decia #1A1A1A mientras la
direccion de diseno dice #16181A. El mismo "negro de marca" salia distinto
segun quien lo pintara.

Lo que se mide aca:
  1. Los tokens existen y valen lo que dice DESIGN_DIRECTION_20260817.md.
  2. tailwind.config.js no contradice al :root en los nombres compartidos.
  3. No aparecen hex nuevos en custom.css fuera de los ya inventariados.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PANEL = Path(__file__).resolve().parent.parent
_CUSTOM = _PANEL / "app" / "static" / "css" / "custom.css"
_TW_CONFIG = _PANEL / "tailwind.config.js"

# De docs/audit/DESIGN_DIRECTION_20260817.md, con el contraste ya verificado ahi.
TOKENS_ESPERADOS = {
    "--paper": "#F7F7F6",
    "--surface": "#FFFFFF",
    "--ink-900": "#16181A",
    "--ink-600": "#55595E",
    "--ink-400": "#6B7075",
    "--rule": "#DFDFDC",
    "--rule-strong": "#8C8C88",
    # El acento de Onnix es el negro de marca: la marca es blanco y negro, y
    # el negro entra como relleno de la accion primaria con texto blanco
    # encima (17,80:1). Lo cubre tests/test_accent_contrast.py.
    "--accent": "#16181A",
    "--accent-dark": "#000000",
    "--accent-ink": "#16181A",
    # Superficie de seleccion. Gris neutro y no un tinte de acento: el acento
    # es negro y no tiene tinte claro propio. 15,05:1 con --ink-900 encima.
    "--accent-wash": "#ECECEA",
    "--danger": "#B42318",
    "--shell": "#16181A",
    "--shell-raised": "#1B1D1F",
    "--shell-ink": "#F2F2F0",
    "--disabled-bg": "#EDEDEA",
    "--disabled-ink": "#55595E",
}

# Los que siguen en hex a proposito, cada uno con el carril que lo resuelve.
HEX_TOLERADOS = {
    # Fondos de aviso de .error-msg (carril B5). No son tokens: son dos tintes
    # muy claros que existen solo para separar «error» de «advertencia» sin
    # meter un tercer matiz saturado en la interfaz. El texto encima si sale
    # del sistema o esta medido: 6,05:1 y 7,21:1.
    "#FEF3F2", "#FFFAEB", "#93370D",
    # Colores muertos que quedan en comentarios del propio CSS, explicando
    # por que se fueron. El regex no distingue comentario de regla.
    "#dc2626", "#d97706", "#16a34a", "#fff",
    # Los seis de .intent-badge se fueron con la regla: era CSS muerto. Si
    # alguno vuelve, este test lo caza. — M3
}


def _root() -> dict[str, str]:
    css = _CUSTOM.read_text(encoding="utf-8")
    bloque = re.search(r":root\s*\{(.*?)\}", css, re.DOTALL)
    assert bloque, "custom.css no tiene bloque :root"
    return {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", bloque.group(1))
    }


@pytest.mark.parametrize("token,valor", sorted(TOKENS_ESPERADOS.items()))
def test_el_token_existe_y_vale_lo_que_dice_la_direccion(token, valor):
    root = _root()
    assert token in root, f"falta {token} en el :root"
    assert root[token].upper() == valor.upper(), (
        f"{token} vale {root[token]}, la direccion de diseno dice {valor}"
    )


def test_el_foco_sale_del_sistema():
    """El anillo de foco es --ink-900, no un hex suelto (WCAG 2.2 1.4.11)."""
    assert _root().get("--focus") == "var(--ink-900)"


def test_tailwind_no_contradice_al_root():
    js = _TW_CONFIG.read_text(encoding="utf-8")
    colores = dict(re.findall(r"'(onnix-[a-z-]+)':\s*'(#[0-9A-Fa-f]{6})'", js))
    root = _root()
    equivalencias = {
        "onnix-accent": "--accent",
        "onnix-black": "--ink-900",
        "onnix-accent-dark": "--accent-dark",
        "onnix-accent-ink": "--accent-ink",
        # Entro a tailwind.config.js el 2026-08-23: el token existia en el
        # :root sin utility, y por eso 13 superficies de seleccion se pintaban
        # `amber-50`. Sin esta fila el hex podria divergir en silencio.
        "onnix-accent-wash": "--accent-wash",
    }
    for nombre, token in equivalencias.items():
        assert nombre in colores, f"tailwind.config.js perdio {nombre}"
        assert colores[nombre].upper() == root[token].upper(), (
            f"{nombre}={colores[nombre]} contra {token}={root[token]}: "
            "vuelven a ser dos paletas"
        )


def test_no_aparecen_hex_nuevos_en_custom_css():
    css = _CUSTOM.read_text(encoding="utf-8")
    permitidos = {v.upper() for v in TOKENS_ESPERADOS.values()} | {
        h.upper() for h in HEX_TOLERADOS
    }
    encontrados = {h.upper() for h in re.findall(r"#[0-9A-Fa-f]{3,8}\b", css)}
    nuevos = encontrados - permitidos
    assert not nuevos, (
        f"hex fuera del sistema en custom.css: {sorted(nuevos)}. "
        "Usar un token del :root, o agregarlo aca con el carril que lo resuelve."
    )

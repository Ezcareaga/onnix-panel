"""Los tokens de las dos paginas publicas y de la landing.

Tres superficies, dos decisiones distintas, y este archivo sostiene las dos.

**Portal ↔ portal: un solo lugar.** La ficha y el listado repetian ~100 lineas
identicas de preambulo —las dos caras de Outfit byte a byte iguales, once
tokens con el mismo nombre y el mismo valor, el reset, la base de `html`/`body`
y el header con su wordmark—. Ahora salen de `public/_estilos_comunes.html`.
Tenerlo dos veces ya habia costado: el comentario de `.wordmark` estaba
completo en la ficha y recortado en el listado, o sea que el porque de A7 solo
vivia en un lado.

**Portal ↔ landing: NO se unifica, se compara.** Medido el 2026-08-23: las dos
superficies comparten **once valores** y solo **dos nombres**. La landing nombra
por apariencia (`--black`, `--dark-card`, `--white`) y el portal por rol
(`--bg`, `--surface-2`, `--text`) — no son dos copias del mismo vocabulario,
son dos vocabularios sobre la misma paleta. Unificar obliga a renombrar uno
entero, y ademas a una request bloqueante o a un build que la landing no tiene.

Lo que la unificacion iba a comprar era que no divirjan. Eso lo compra este
archivo, mas barato: **lee los dos archivos y compara los valores**. Ninguno
esta escrito a mano aca.

Y ya paso: `--border-control` existia en la landing y el portal no lo tenia, y
esa divergencia dejo el buscador del portal en 1,28:1 contra 3,46:1 el de la
landing. Esta escrito en el `:root` del listado.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[2]
_PUBLIC = _RAIZ / "panel" / "app" / "templates" / "public"
_PARTIAL = _PUBLIC / "_estilos_comunes.html"
_FICHA = _PUBLIC / "property.html"
_LISTADO = _PUBLIC / "propiedades.html"
_LANDING = _RAIZ / "landing" / "assets" / "css" / "styles.css"

# La misma paleta con dos vocabularios. Los NOMBRES son el dato de esta tabla;
# los VALORES se leen de los archivos y se comparan.
EQUIVALENCIAS = [
    ("black", "bg"),
    ("dark", "surface-1"),
    ("dark-card", "surface-2"),
    ("dark-border", "border"),
    ("gray-light", "text-sec"),
    ("white", "text"),
    ("accent", "accent"),
    ("accent-light", "accent-hover"),
    ("accent-dark", "accent-press"),
    ("font-body", "sans"),
    ("radius", "radius"),
]

# Los que el partial declara y ningun template puede volver a declarar.
COMPARTIDOS_DEL_PORTAL = {
    "bg", "surface-1", "surface-2", "border", "accent", "accent-hover",
    "accent-press", "text", "text-sec", "sans", "radius",
}


def _root(texto: str) -> dict[str, str]:
    """Los tokens del primer bloque `:root` de un archivo."""
    m = re.search(r":root\s*\{(.*?)\n\s*\}", texto, re.DOTALL)
    assert m, "no hay bloque :root"
    return {k: v.strip() for k, v in re.findall(r"--([a-z0-9-]+):\s*([^;]+);", m.group(1))}


def _roots(texto: str) -> list[dict[str, str]]:
    """Todos los bloques `:root` — un template puede tener el suyo ademas del
    que trae el partial."""
    return [
        {k: v.strip() for k, v in re.findall(r"--([a-z0-9-]+):\s*([^;]+);", cuerpo)}
        for cuerpo in re.findall(r":root\s*\{(.*?)\n\s*\}", texto, re.DOTALL)
    ]


# ---------------------------------------------------------------------------
# Portal ↔ landing: comparar, no unificar
# ---------------------------------------------------------------------------

class TestLaMismaPaletaConDosVocabularios:
    @pytest.mark.parametrize("en_landing,en_portal", EQUIVALENCIAS)
    def test_el_valor_es_el_mismo_de_los_dos_lados(self, en_landing, en_portal):
        landing = _root(_LANDING.read_text(encoding="utf-8"))
        portal = _root(_PARTIAL.read_text(encoding="utf-8"))
        assert en_landing in landing, f"la landing perdio --{en_landing}"
        assert en_portal in portal, f"el partial del portal perdio --{en_portal}"
        assert landing[en_landing].upper() == portal[en_portal].upper(), (
            f"--{en_landing} de la landing vale {landing[en_landing]} y "
            f"--{en_portal} del portal vale {portal[en_portal]}: es el mismo "
            "color con dos nombres, y acaba de divergir"
        )

    def test_la_tabla_cubre_todo_lo_que_las_dos_comparten(self):
        """Sin esto, un token nuevo compartido queda afuera de la comparacion y
        el archivo protege menos de lo que su nombre dice."""
        landing = _root(_LANDING.read_text(encoding="utf-8"))
        portal = _root(_PARTIAL.read_text(encoding="utf-8"))
        por_valor: dict[str, set[str]] = {}
        for origen, tokens in (("landing", landing), ("portal", portal)):
            for k, v in tokens.items():
                por_valor.setdefault(v.upper(), set()).add(origen)
        compartidos = {v for v, o in por_valor.items() if o == {"landing", "portal"}}
        cubiertos = {
            landing[a].upper() for a, b in EQUIVALENCIAS
            if a in landing and b in portal
        }
        sin_cubrir = compartidos - cubiertos
        assert not sin_cubrir, (
            f"valores que las dos superficies comparten y la tabla no lista: "
            f"{sorted(sin_cubrir)}. Agregalos a EQUIVALENCIAS o van a divergir "
            "sin que nadie se entere"
        )


# ---------------------------------------------------------------------------
# Portal ↔ portal: un solo lugar
# ---------------------------------------------------------------------------

class TestUnSoloPreambulo:
    @pytest.mark.parametrize("template", [_FICHA, _LISTADO], ids=["ficha", "listado"])
    def test_el_template_incluye_el_partial(self, template):
        assert '{% include "public/_estilos_comunes.html" %}' in template.read_text(
            encoding="utf-8"
        ), f"{template.name} dejo de incluir el preambulo comun"

    @pytest.mark.parametrize("template", [_FICHA, _LISTADO], ids=["ficha", "listado"])
    def test_el_template_no_redeclara_un_token_compartido(self, template):
        propios: set[str] = set()
        for bloque in _roots(template.read_text(encoding="utf-8")):
            propios |= set(bloque)
        repetidos = propios & COMPARTIDOS_DEL_PORTAL
        assert not repetidos, (
            f"{template.name} vuelve a declarar {sorted(repetidos)}, que ya "
            "vienen del partial: es la copia que despues diverge"
        )

    @pytest.mark.parametrize("template", [_FICHA, _LISTADO], ids=["ficha", "listado"])
    def test_el_template_no_repite_las_caras_de_outfit(self, template):
        """Las dos `@font-face` son identicas en las dos paginas. Un test viejo
        se conformaba con que UNA de las dos fuera variable justamente porque
        habia dos copias y miraba la que le tocara."""
        assert "@font-face" not in template.read_text(encoding="utf-8"), (
            f"{template.name} volvio a declarar @font-face: las dos caras de "
            "Outfit viven en el partial"
        )

    def test_el_partial_declara_las_dos_caras_variables(self):
        fuente = _PARTIAL.read_text(encoding="utf-8")
        caras = re.findall(r"@font-face\s*\{(.*?)\}", fuente, re.DOTALL)
        assert len(caras) == 2, f"{len(caras)} @font-face en el partial, esperaba 2"
        for cara in caras:
            assert re.search(r"font-weight:\s*100 900", cara), (
                "una de las dos caras dejo de ser variable"
            )
            assert "unicode-range" in cara, "una cara perdio su unicode-range"

    def test_el_partial_trae_el_header_una_sola_vez(self):
        fuente = _PARTIAL.read_text(encoding="utf-8")
        for selector in (".site-header", ".header-inner", ".wordmark"):
            assert fuente.count(selector + " {") == 1, (
                f"{selector} aparece {fuente.count(selector + ' {')} veces en "
                "el partial"
            )
        for template in (_FICHA, _LISTADO):
            texto = template.read_text(encoding="utf-8")
            assert ".site-header {" not in texto, (
                f"{template.name} volvio a definir .site-header"
            )

    @pytest.mark.parametrize("token,template", [
        ("max-w", _FICHA), ("max-w", _LISTADO),
        ("border-lb", _FICHA), ("wa-green", _FICHA),
        ("border-control", _LISTADO),
    ], ids=["max-w-ficha", "max-w-listado", "border-lb", "wa-green", "border-control"])
    def test_lo_que_NO_es_comun_se_queda_en_su_template(self, token, template):
        """`--max-w` vale 960 en la ficha y 1180 en el listado: subirlo al
        partial las pinta iguales."""
        propios: set[str] = set()
        for bloque in _roots(template.read_text(encoding="utf-8")):
            propios |= set(bloque)
        assert token in propios, f"{template.name} perdio --{token}"
        assert token not in _root(_PARTIAL.read_text(encoding="utf-8")), (
            f"--{token} subio al partial y no es comun a las dos paginas"
        )

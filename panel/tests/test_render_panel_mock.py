"""El renderizador de pantallas con datos inventados no puede filtrar PII.

`scripts/render_panel_mock.py` existe para que sacar una captura del panel no
obligue a abrir staging, que es producción restaurada sin anonimizar. Eso lo
convierte en el único lugar del repo donde un dato de cliente podría salir
publicado en un video sin que nadie lo note.

Por eso los tests son dos cosas distintas:

1. **Que renderice.** HTML vacío, un `{{` sin resolver o un `Undefined` de
   Jinja son fallas del renderizador.
2. **Que los datos sean inventados y se pueda demostrar.** Los teléfonos del
   mock viven en un bloque reservado —`+595 9XX 000 0NN`— que ningún número
   asignado en Paraguay tiene. La prueba negativa es meter un teléfono con
   formato de real en los datos mock: este archivo se pone rojo.
"""
from __future__ import annotations

import importlib.util
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "render_panel_mock.py"


def _modulo():
    spec = importlib.util.spec_from_file_location("render_panel_mock", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rpm = _modulo()

# Las pantallas que el capturador de video espera encontrar en el manifest.
# Va escrito a mano y aparte de la parametrización de abajo a propósito: un
# test parametrizado sobre `pantallas()` no puede ver que una pantalla
# desapareció —le borra el caso—, así que la lista que es el contrato se
# nombra acá.
PANTALLAS_ESPERADAS = [
    "login",
    "dashboard",
    "conversaciones",
    "leads",
    "propiedades-listado",
    "propiedades-ficha",
    "contactos-listado",
    "contactos-detalle",
    "stats",
    "settings",
]

# +595 9XX 000 0NN — el bloque reservado de `render_panel_mock.py`.
_RESERVADO = re.compile(r"^\+595 9\d\d 000 0\d\d$")
# Cualquier cosa con forma de móvil paraguayo, en E.164 o en formato local.
_TELEFONO = re.compile(
    r"\+?595[\s.-]?9\d{2}[\s.-]?\d{3}[\s.-]?\d{3}"
    r"|(?<!\d)09\d{2}[\s.-]?\d{3}[\s.-]?\d{3}(?!\d)"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


def _cadenas(valor, ruta="ctx"):
    """Cada string que llega al render, con el camino donde apareció."""
    if isinstance(valor, str):
        yield ruta, valor
    elif isinstance(valor, dict):
        for k, v in valor.items():
            yield from _cadenas(k, f"{ruta}.{k}")
            yield from _cadenas(v, f"{ruta}.{k}")
    elif isinstance(valor, (list, tuple, set, frozenset)):
        for i, v in enumerate(valor):
            yield from _cadenas(v, f"{ruta}[{i}]")
    elif isinstance(valor, SimpleNamespace):
        yield from _cadenas(vars(valor), ruta)
    elif isinstance(valor, (int, float, Decimal, datetime, bool, type(None))):
        return


def _todas_las_cadenas():
    for p in rpm.pantallas():
        yield from _cadenas(p.ctx, p.id)


@pytest.fixture(scope="module")
def jinja():
    return rpm.env()


@pytest.fixture(scope="module")
def renderizadas(jinja):
    return {p.id: rpm.render(p, jinja) for p in rpm.pantallas()}


def test_las_pantallas_del_manifest_son_las_esperadas():
    assert [p.id for p in rpm.pantallas()] == PANTALLAS_ESPERADAS


@pytest.mark.parametrize("pantalla_id", PANTALLAS_ESPERADAS)
def test_la_pantalla_renderiza_entera(pantalla_id, renderizadas):
    html = renderizadas[pantalla_id]
    assert len(html) > 2000, "el HTML salió vacío o casi"
    assert "{{" not in html, "quedó una expresión de Jinja sin resolver"
    assert "{%" not in html, "quedó una etiqueta de Jinja sin resolver"
    assert "Undefined" not in html, "un dato mock falta y Jinja lo dijo en el HTML"
    assert "</html>" in html


@pytest.mark.parametrize("pantalla_id", PANTALLAS_ESPERADAS)
def test_la_pantalla_no_lleva_rutas_absolutas_a_los_estaticos(
    pantalla_id, renderizadas
):
    """El HTML se abre con `file://` desde cualquier directorio."""
    html = renderizadas[pantalla_id]
    assert '"/static/' not in html
    assert "'/static/" not in html
    assert "static/css/tailwind.css" in html


@pytest.mark.parametrize("pantalla_id", PANTALLAS_ESPERADAS)
def test_la_pantalla_no_dispara_pedidos_sola(pantalla_id, renderizadas):
    """Un `hx-trigger` de `load` o de `every Ns` pide una URL que no existe en
    un directorio estático, y HTMX mete el 404 adentro de la pantalla."""
    html = renderizadas[pantalla_id]
    assert 'hx-trigger="load' not in html
    assert "every 60s" not in html


def test_los_telefonos_mock_son_del_bloque_reservado():
    """La prueba negativa: cambiar uno por `+595 981 234 567` pone esto rojo."""
    encontrados = 0
    for ruta, texto in _todas_las_cadenas():
        for tel in _TELEFONO.findall(texto):
            encontrados += 1
            assert _RESERVADO.match(tel), (
                f"{ruta}: «{tel}» tiene formato de teléfono real. Los datos "
                f"mock usan el bloque reservado +595 9XX 000 0NN."
            )
    assert encontrados >= 8, "no se encontró ningún teléfono: el walk no camina"


def test_los_correos_mock_van_a_un_dominio_reservado():
    """`.test` está reservado por la RFC 6761: no le llega mail a nadie."""
    encontrados = 0
    for ruta, texto in _todas_las_cadenas():
        for mail in _EMAIL.findall(texto):
            encontrados += 1
            assert mail.endswith(".test"), (
                f"{ruta}: «{mail}» no está en un dominio reservado."
            )
    assert encontrados >= 5, "no se encontró ningún correo: el walk no camina"


def test_dos_corridas_dan_los_mismos_bytes(tmp_path):
    """El que consume la salida es un pipeline de video: un pixel que cambia
    solo es un video que cambia solo."""
    una, otra = tmp_path / "a", tmp_path / "b"
    assert rpm.main(["--out", str(una), "--css-from", ""]) == 0
    assert rpm.main(["--out", str(otra), "--css-from", ""]) == 0

    archivos = sorted(p.name for p in una.glob("*"))
    assert "manifest.json" in archivos
    for nombre in archivos:
        a, b = una / nombre, otra / nombre
        if a.is_dir():
            continue
        assert a.read_bytes() == b.read_bytes(), f"{nombre} cambió entre corridas"


def test_el_manifest_lista_cada_pantalla_con_su_archivo(tmp_path):
    import json

    assert rpm.main(["--out", str(tmp_path), "--css-from", ""]) == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert [p["id"] for p in manifest["pantallas"]] == PANTALLAS_ESPERADAS
    for pantalla in manifest["pantallas"]:
        assert (tmp_path / pantalla["archivo"]).exists()
        assert pantalla["titulo"] and pantalla["descripcion"] and pantalla["ruta_real"]

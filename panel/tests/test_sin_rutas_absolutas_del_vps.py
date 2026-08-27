"""Nadie puede buscar CÓDIGO en el home del VPS escribiendo la ruta a mano.

El 2026-08-18 el repo salió de `/home/onnix` y 54 tests se pusieron rojos de
golpe. Ninguno falló por el código que decía cubrir: fallaron porque tenían
escrita a mano la ruta donde el repo vivía en ese momento. Es el caso de
"test acoplado a lo accidental" del CLAUDE.md, cincuenta veces seguidas.

Una regla en prosa es una intención. Esto es el chequeo que rompe el build.

**Lo que sí está permitido** es apuntar al home cuando lo que se busca es
estado del servidor y no código: `.env`, `logs/`, `backups/`, `images/`,
`.venv/`. Eso no se movió y no se mueve.

Se filtran los comentarios antes de assertar: si no, este archivo fallaría
contra su propia documentación — trampa que ya mordió tres veces en este repo.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DIRECTORIOS = ("panel/app", "panel/tests", "scrapers", "scripts")
EXTENSIONES = (".py", ".sh")

# Partido en dos para que el literal completo no exista en este archivo y el
# test no se encuentre a sí mismo por el camino de los strings.
HOME_VPS = "/home/" + "onnix"

# Lo que vive en el home a propósito: estado del servidor, no código.
ESTADO = ("logs", "backups", "images", ".venv", ".env", ".ssh", ".config", ".cache", "local")

# Generadores de workflows de n8n. N8N está descomisionado desde `dbce3cd` y no
# se reinstala; estos quedaron apuntando a rutas que ya no existen. Se excluyen
# en vez de arreglarse porque arreglarlos sería mantener algo muerto — pero se
# nombran acá para que la exclusión sea una decisión visible y no un olvido.
MUERTOS = (
    "scripts/fase2_update_workflows.py",
    "scripts/fase3_update_workflows.py",
    "scripts/fase4_update_workflows.py",
    "scripts/setup_n8n_credentials.sh",
)

# `scripts/archive/` es lo que su nombre dice. No se escanea.
DIRECTORIOS_MUERTOS = ("scripts/archive",)

# Un `/home/onnix` sin segmento después (fin de línea, `}`, comilla) es la
# raíz del estado — `${ONNIX_STATE_DIR:-/home/onnix}` es exactamente eso.
# Un dotfile suelto en la raíz también es estado (`.tg_session…`).
_SEGMENTO = re.compile(re.escape(HOME_VPS) + r"(?:/([A-Za-z0-9._-]+))?")


_TRIPLE = re.compile(r'("""|\'\'\')')


def _lineas_de_codigo(texto: str):
    """Numeradas, sin comentarios ni docstrings.

    Los docstrings importan: media docena de scripts documentan su uso con la
    ruta vieja adentro de un triple-quoted, y eso es prosa, no código.
    """
    dentro = None
    for n, linea in enumerate(texto.splitlines(), 1):
        resto = linea
        if dentro:
            if dentro in resto:
                resto = resto.split(dentro, 1)[1]
                dentro = None
            else:
                continue
        m = _TRIPLE.search(resto)
        while m:
            marca = m.group(1)
            despues = resto[m.end():]
            if marca in despues:                      # abre y cierra en la misma línea
                resto = resto[: m.start()] + despues.split(marca, 1)[1]
                m = _TRIPLE.search(resto)
            else:
                resto = resto[: m.start()]
                dentro = marca
                m = None
        despojada = resto.strip()
        if despojada.startswith("#"):
            continue
        yield n, resto.split("  #")[0]


def _apunta_a_codigo(linea: str) -> bool:
    for m in _SEGMENTO.finditer(linea):
        segmento = m.group(1)
        if segmento is None:            # la raíz pelada: estado
            continue
        if segmento in ESTADO:
            continue
        if segmento.startswith("."):    # dotfile suelto en la raíz: estado
            continue
        return True
    return False


def _archivos():
    for d in DIRECTORIOS:
        base = REPO_ROOT / d
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if f.suffix not in EXTENSIONES or "__pycache__" in f.parts:
                continue
            if f.name == Path(__file__).name:
                continue
            rel = str(f.relative_to(REPO_ROOT))
            if rel in MUERTOS or any(rel.startswith(d + "/") for d in DIRECTORIOS_MUERTOS):
                continue
            yield f


def test_nadie_busca_codigo_en_el_home_del_vps():
    culpables = []
    for f in _archivos():
        texto = f.read_text(encoding="utf-8", errors="replace")
        if HOME_VPS not in texto:
            continue
        for n, linea in _lineas_de_codigo(texto):
            if HOME_VPS in linea and _apunta_a_codigo(linea):
                culpables.append(f"{f.relative_to(REPO_ROOT)}:{n}: {linea.strip()[:90]}")
    assert not culpables, (
        "Rutas absolutas al home del VPS apuntando a código (los comentarios no "
        "cuentan, y el estado del servidor está permitido). Resolvé desde "
        "__file__ / BASH_SOURCE o desde una variable de entorno:\n  "
        + "\n  ".join(culpables)
    )

"""Los scripts de imágenes escriben en el estado del servidor, no en el código.

`/home/onnix` dejó de ser el árbol de git el 2026-08-18: el código pasó a
`/srv/onnix/prod` y el home quedó para `.env`, `images/`, `logs/` y `backups/`.
Estos dos scripts seguían derivando sus rutas del directorio del repo, así que
las fotos habrían caído en `/srv/onnix/prod/images` — que nginx no sirve, que el
backup a Drive no toma, y que el `git checkout` de cada deploy deja atrás.

No se notó antes porque ninguno de los dos corrió desde la reconstrucción. Se
notó al agendarlos.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

SCRIPTS = {
    "download_images": "image_download.log",
    "cleanup_images": "cleanup.log",
    "renumber_images": "renumber_images.log",
}


def _cargar(nombre: str):
    """Importa el script por ruta, con las constantes ya resueltas."""
    spec = importlib.util.spec_from_file_location(
        f"_onnix_{nombre}", REPO / "scripts" / f"{nombre}.py"
    )
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.mark.parametrize("nombre,log", sorted(SCRIPTS.items()))
def test_las_rutas_salen_de_onnix_state_dir(nombre, log, monkeypatch, tmp_path):
    monkeypatch.setenv("ONNIX_STATE_DIR", str(tmp_path))

    modulo = _cargar(nombre)

    assert modulo.IMAGES_DIR == tmp_path / "images"
    assert modulo.LOG_FILE == tmp_path / "logs" / log


@pytest.mark.parametrize("nombre", sorted(SCRIPTS))
def test_el_default_es_el_home_y_no_el_arbol_de_codigo(nombre, monkeypatch):
    monkeypatch.delenv("ONNIX_STATE_DIR", raising=False)

    modulo = _cargar(nombre)

    assert modulo.IMAGES_DIR == Path("/home/onnix/images")
    assert REPO not in modulo.IMAGES_DIR.parents

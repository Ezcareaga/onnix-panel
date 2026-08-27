"""La foto se numera por la descarga que salió bien, no por el índice de la URL.

`scripts/download_images.py` nombraba cada archivo con el índice de la URL en
`image_urls` y mandaba a `local_image_count` solo las descargas exitosas. Con las
primeras N URLs caídas, en disco quedaba `N+1.webp, N+2.webp…` y en la DB
`len(urls) - N`. El lector asume lo contrario —`property_service` pide
`1..local_image_count` y el portal pide `1.webp`— así que servía 404 sobre
archivos que existían: 123 URLs rotas en 46 propiedades, medido en producción
(27227 con `2.webp` y db=1; 43295 con `19..56.webp` y db=38).

El caso que importa es el que fallaba: las primeras URLs caídas. El caso donde
todo baja bien está para pinnear que a las propiedades sanas el fix no les cambia
el nombre de ningún archivo.
"""
from __future__ import annotations

import asyncio
import importlib.util
import io
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[2]

COLORES = [(220, 20, 20), (20, 200, 20), (20, 20, 220), (220, 220, 20)]


def _cargar(nombre: str):
    """Importa un script de scripts/ por ruta, sin paquete."""
    spec = importlib.util.spec_from_file_location(
        f"_onnix_{nombre}", REPO / "scripts" / f"{nombre}.py"
    )
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _imagen(color: tuple[int, int, int], formato: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, format=formato)
    return buf.getvalue()


def _color(path: Path) -> tuple[int, int, int]:
    """Color de una foto en disco — identifica CUÁL imagen quedó en ese nombre."""
    with Image.open(path) as img:
        return img.convert("RGB").getpixel((20, 15))


def _es(path: Path, color: tuple[int, int, int]) -> bool:
    return all(abs(a - b) <= 16 for a, b in zip(_color(path), color))


def _nombres(prop_dir: Path) -> list[str]:
    return sorted((p.name for p in prop_dir.iterdir()), key=lambda n: int(n.split(".")[0]))


# ── aiohttp mínimo: url -> (status, bytes) ──────────────────────────────────

class _RespuestaFalsa:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read(self) -> bytes:
        return self._body


class _SesionFalsa:
    def __init__(self, respuestas: dict[str, tuple[int, bytes]]):
        self._respuestas = respuestas
        self.pedidas: list[str] = []

    def get(self, url: str, **kwargs):
        self.pedidas.append(url)
        return _RespuestaFalsa(*self._respuestas[url])


async def _bajar(dl, tmp_path: Path, respuestas: dict[str, tuple[int, bytes]], urls: list[str]):
    prop = {"source": "remax", "external_id": "50345", "image_urls": urls}
    sesion = _SesionFalsa(respuestas)
    guardadas, errores = await dl.process_property(
        sesion, asyncio.Semaphore(dl.MAX_CONCURRENT), prop
    )
    return guardadas, errores, tmp_path / "remax" / "50345", sesion


# ── El downloader ───────────────────────────────────────────────────────────

class TestNumeracionAlDescargar:
    async def test_las_primeras_urls_caidas_no_dejan_hueco(self, tmp_path, monkeypatch):
        """El caso de 50345: 7 URLs, las primeras fallan, en disco 7.webp y 8.webp."""
        dl = _cargar("download_images")
        monkeypatch.setattr(dl, "IMAGES_DIR", tmp_path)

        urls = [f"https://cdn.test/{i}.jpg" for i in range(4)]
        guardadas, errores, prop_dir, _ = await _bajar(
            dl,
            tmp_path,
            {
                urls[0]: (404, b""),
                urls[1]: (500, b""),
                urls[2]: (200, _imagen(COLORES[2], "png")),
                urls[3]: (200, _imagen(COLORES[3], "png")),
            },
            urls,
        )

        assert _nombres(prop_dir) == ["1.webp", "2.webp"]
        # `guardadas` es lo que mark_downloaded escribe en local_image_count:
        # tiene que ser exactamente la cantidad de archivos, y los archivos
        # tienen que ser 1..guardadas.
        assert (guardadas, errores) == (2, 2)
        assert len(list(prop_dir.iterdir())) == guardadas
        # El orden relativo se conserva: la 3ª URL es la primera foto.
        assert _es(prop_dir / "1.webp", COLORES[2])
        assert _es(prop_dir / "2.webp", COLORES[3])

    async def test_una_url_caida_en_el_medio_tampoco_deja_hueco(self, tmp_path, monkeypatch):
        dl = _cargar("download_images")
        monkeypatch.setattr(dl, "IMAGES_DIR", tmp_path)

        urls = [f"https://cdn.test/{i}.jpg" for i in range(3)]
        guardadas, errores, prop_dir, _ = await _bajar(
            dl,
            tmp_path,
            {
                urls[0]: (200, _imagen(COLORES[0], "png")),
                urls[1]: (404, b""),
                urls[2]: (200, _imagen(COLORES[2], "png")),
            },
            urls,
        )

        assert _nombres(prop_dir) == ["1.webp", "2.webp"]
        assert (guardadas, errores) == (2, 1)
        assert _es(prop_dir / "1.webp", COLORES[0])
        assert _es(prop_dir / "2.webp", COLORES[2])

    async def test_sin_fallas_la_numeracion_no_cambia(self, tmp_path, monkeypatch):
        """Las propiedades sanas siguen igual: 1..N en el orden de image_urls."""
        dl = _cargar("download_images")
        monkeypatch.setattr(dl, "IMAGES_DIR", tmp_path)

        urls = [f"https://cdn.test/{i}.jpg" for i in range(4)]
        guardadas, errores, prop_dir, _ = await _bajar(
            dl, tmp_path, {u: (200, _imagen(c, "png")) for u, c in zip(urls, COLORES)}, urls
        )

        assert _nombres(prop_dir) == ["1.webp", "2.webp", "3.webp", "4.webp"]
        assert (guardadas, errores) == (4, 0)
        for i, color in enumerate(COLORES, 1):
            assert _es(prop_dir / f"{i}.webp", color)

    async def test_todas_caidas_no_escribe_nada(self, tmp_path, monkeypatch):
        dl = _cargar("download_images")
        monkeypatch.setattr(dl, "IMAGES_DIR", tmp_path)

        urls = [f"https://cdn.test/{i}.jpg" for i in range(2)]
        guardadas, errores, prop_dir, _ = await _bajar(
            dl, tmp_path, {u: (404, b"") for u in urls}, urls
        )

        assert list(prop_dir.iterdir()) == []
        assert (guardadas, errores) == (0, 2)


# ── El reparador ────────────────────────────────────────────────────────────

def _dir_con(tmp_path: Path, numeros: list[int]) -> Path:
    prop_dir = tmp_path / "onnixpy" / "27227"
    prop_dir.mkdir(parents=True)
    for n, color in zip(numeros, COLORES):
        (prop_dir / f"{n}.webp").write_bytes(_imagen(color, "webp"))
    return prop_dir


class TestReparador:
    def test_renumera_desde_uno_conservando_el_orden(self, tmp_path):
        """El caso de 48337: en disco 5.webp y 6.webp, tienen que ser 1 y 2."""
        rn = _cargar("renumber_images")
        prop_dir = _dir_con(tmp_path, [5, 6])

        archivos, renombrados = rn.renumerar_dir(prop_dir)

        assert _nombres(prop_dir) == ["1.webp", "2.webp"]
        assert (archivos, renombrados) == (2, 2)
        assert _es(prop_dir / "1.webp", COLORES[0])
        assert _es(prop_dir / "2.webp", COLORES[1])

    def test_renumera_sin_pisar_un_nombre_ocupado(self, tmp_path):
        """2,3,4 → 1,2,3: cada destino está ocupado hasta un instante antes."""
        rn = _cargar("renumber_images")
        prop_dir = _dir_con(tmp_path, [2, 3, 4])

        archivos, renombrados = rn.renumerar_dir(prop_dir)

        assert _nombres(prop_dir) == ["1.webp", "2.webp", "3.webp"]
        assert (archivos, renombrados) == (3, 3)
        for i, color in enumerate(COLORES[:3], 1):
            assert _es(prop_dir / f"{i}.webp", color)

    def test_es_idempotente(self, tmp_path):
        rn = _cargar("renumber_images")
        prop_dir = _dir_con(tmp_path, [5, 6])

        rn.renumerar_dir(prop_dir)
        assert rn.renumerar_dir(prop_dir) == (2, 0)
        assert _nombres(prop_dir) == ["1.webp", "2.webp"]

    def test_no_toca_lo_que_ya_esta_bien(self, tmp_path):
        rn = _cargar("renumber_images")
        prop_dir = _dir_con(tmp_path, [1, 2, 3])
        antes = {p.name: p.stat().st_mtime_ns for p in prop_dir.iterdir()}

        assert rn.renumerar_dir(prop_dir) == (3, 0)
        assert {p.name: p.stat().st_mtime_ns for p in prop_dir.iterdir()} == antes

    def test_dry_run_no_escribe(self, tmp_path):
        rn = _cargar("renumber_images")
        prop_dir = _dir_con(tmp_path, [5, 6])

        archivos, renombrados = rn.renumerar_dir(prop_dir, dry_run=True)

        assert (archivos, renombrados) == (2, 2)
        assert _nombres(prop_dir) == ["5.webp", "6.webp"]

    def test_ignora_lo_que_no_es_una_foto_numerada(self, tmp_path):
        rn = _cargar("renumber_images")
        prop_dir = _dir_con(tmp_path, [3])
        (prop_dir / "portada.webp").write_bytes(_imagen(COLORES[3], "webp"))

        archivos, renombrados = rn.renumerar_dir(prop_dir)

        assert (archivos, renombrados) == (1, 1)
        assert (prop_dir / "1.webp").exists()
        assert (prop_dir / "portada.webp").exists()

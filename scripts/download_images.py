#!/usr/bin/env python3
"""Download property images from CDNs, convert to WebP, store locally.

Usage:
    python3 scripts/download_images.py                  # All pending
    python3 scripts/download_images.py --limit 10       # First 10
    python3 scripts/download_images.py --source remax   # Only REMAX
    python3 scripts/download_images.py --new-only       # Same as default (images_downloaded=false)
"""

import argparse
import asyncio
import io
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from PIL import Image

# ── Config ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scrapers"))

from shared.config import (
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_DB,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
)

# Las fotos y los logs son estado del servidor, no código: viven en
# /home/onnix desde que el repo salió del home (2026-08-18). Apuntados al
# árbol de código, las fotos caían en /srv/onnix/prod/images —que nginx no sirve y
# el backup no toma— y cada deploy las dejaba atrás. Misma variable que usan los
# scripts de bash.
STATE_DIR = Path(os.environ.get("ONNIX_STATE_DIR", "/home/onnix"))
IMAGES_DIR = STATE_DIR / "images"
LOG_FILE = STATE_DIR / "logs" / "image_download.log"
MAX_WIDTH = 800
WEBP_QUALITY = 80
MAX_CONCURRENT = 10
DOWNLOAD_TIMEOUT = 10  # seconds per image
VALID_SOURCES = ("remax", "onnixpy", "coldwell", "psir")

# ── Logging ─────────────────────────────────────────────────────────────
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [IMG_DOWNLOAD] %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers = [logging.StreamHandler()]
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt=LOG_DATEFMT,
                        handlers=handlers)


logger = logging.getLogger("img_download")

# ── Database (sync psycopg) ─────────────────────────────────────────────
import psycopg
from psycopg.rows import dict_row


def get_conn():
    return psycopg.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        row_factory=dict_row,
    )


def fetch_pending(conn, source: str | None, limit: int | None) -> list[dict]:
    """Get properties needing image download."""
    sql = """
        SELECT id, source, external_id, image_urls
        FROM properties
        WHERE images_downloaded = false
          AND is_active = true
          AND image_urls IS NOT NULL
          AND array_length(image_urls, 1) > 0
    """
    params: list = []
    if source:
        sql += " AND source = %s"
        params.append(source)
    # Lo mas nuevo primero, y no `source, id`.
    #
    # `ORDER BY source, id` es lo mas viejo primero: un alta nueva es el `id`
    # mas alto de su fuente, o sea el ultimo lugar de la cola. Combinado con una
    # sola corrida diaria y `timeout 80m`, **ninguna corrida del cron termino
    # nunca** desde que se instalo el crontab el 2026-08-20: el 21/08 llego a
    # 700 de 6.807 pendientes y el 22/08 a 600 de 6.158. A ritmo medido —7,6 a
    # 9,9 propiedades por minuto— vaciar esa cola son 11 a 15 horas y el cron
    # tiene 80 minutos. Nunca podia terminar, y lo que quedaba afuera era
    # siempre lo recien dado de alta, que es justo lo que encabeza el listado.
    #
    # Medido el 2026-08-24 sobre las altas de los ultimos 7 dias con descarga
    # hecha (n=2.178): **mediana 65 horas** hasta la foto, y solo el 10 % la
    # recibe dentro de las 12. Ninguna de esas descargas la hizo el cron: las
    # colas largas terminan todas en un backfill manual.
    #
    # Con `created_at DESC` una corrida cortada por timeout cubre siempre lo que
    # esta arriba del listado, que es lo unico que se ve.
    #
    # Es una regresion, no un bug nuevo: el CHANGELOG del 2026-06-11 ya lo habia
    # arreglado —«closing the up-to-24h no-photo window for newly scraped props
    # that headline the public portal»— y el crontab del VPS nuevo volvio a
    # `30 1 * * *`. El orden de la cola es la mitad del arreglo; la otra mitad
    # es la frecuencia, que vive en el crontab.
    sql += " ORDER BY created_at DESC"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def mark_downloaded(conn, prop_id: int, count: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE properties
               SET images_downloaded = true,
                   images_downloaded_at = %s,
                   local_image_count = %s
               WHERE id = %s""",
            (datetime.now(timezone.utc), count, prop_id),
        )
    conn.commit()


# ── Image processing ────────────────────────────────────────────────────

def convert_to_webp(data: bytes) -> bytes | None:
    """Resize to max MAX_WIDTH and convert to WebP."""
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        w, h = img.size
        if w > MAX_WIDTH:
            ratio = MAX_WIDTH / w
            img = img.resize((MAX_WIDTH, int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="webp", quality=WEBP_QUALITY)
        return buf.getvalue()
    except Exception as e:
        logger.warning("WebP conversion failed: %s", e)
        return None


# ── Download ─────────────────────────────────────────────────────────────

async def download_one(session: aiohttp.ClientSession, url: str) -> bytes | None:
    """Download a single image URL, return raw bytes or None."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)) as resp:
            if resp.status != 200:
                logger.warning("HTTP %d for %s", resp.status, url)
                return None
            return await resp.read()
    except asyncio.TimeoutError:
        logger.warning("Timeout downloading %s", url)
        return None
    except Exception as e:
        logger.warning("Error downloading %s: %s", url, e)
        return None


async def process_property(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    prop: dict,
) -> tuple[int, int]:
    """Download + convert all images for one property. Returns (downloaded, errors).

    El nombre del archivo sale de un contador que avanza SOLO cuando la imagen se
    guardó, no del índice de la URL en image_urls. Numerando por índice, dos URLs
    caídas al principio dejaban `3.webp, 4.webp` en disco y `local_image_count=2`
    en la DB; el lector pide `1..local_image_count` y servía 404 sobre archivos
    que existían (123 URLs en 46 propiedades, agosto 2026).

    Se fue con el atajo de saltear `out_path.exists()`: con numeración secuencial
    el nombre no se conoce antes de bajar la imagen, y el atajo solo servía para
    una corrida cortada a la mitad —las propiedades que llegan acá tienen
    images_downloaded=false— así que el precio es rebajar esas fotos.
    """
    source = prop["source"]
    ext_id = prop["external_id"]
    urls = prop["image_urls"]
    prop_dir = IMAGES_DIR / source / ext_id
    prop_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    errors = 0

    for url in urls:
        async with semaphore:
            raw = await download_one(session, url)

        if raw is None:
            errors += 1
            continue

        webp_data = convert_to_webp(raw)
        if webp_data is None:
            errors += 1
            continue

        downloaded += 1
        (prop_dir / f"{downloaded}.webp").write_bytes(webp_data)

    return downloaded, errors


async def run(args: argparse.Namespace) -> None:
    conn = get_conn()
    props = fetch_pending(conn, args.source, args.limit)
    total = len(props)

    if total == 0:
        logger.info("No pending properties to download")
        conn.close()
        return

    logger.info("Starting download: %d properties pending", total)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    stats = {"props": 0, "images": 0, "errors": 0}

    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT, limit_per_host=5)
    async with aiohttp.ClientSession(
        connector=connector,
        headers={"User-Agent": "OnnixSA-ImageDL/1.0"},
    ) as session:
        for i, prop in enumerate(props, 1):
            dl, err = await process_property(session, semaphore, prop)
            if dl == 0:
                # All downloads failed (CDN 404/timeout): do NOT mark as
                # downloaded so the next --new-only run retries this prop.
                logger.warning(
                    "0 images downloaded for prop id=%s (%s/%s, %d errors) — "
                    "not marking as downloaded, will retry next run",
                    prop["id"], prop["source"], prop["external_id"], err,
                )
            else:
                mark_downloaded(conn, prop["id"], dl)
            stats["props"] += 1
            stats["images"] += dl
            stats["errors"] += err

            if i % 100 == 0 or i == total:
                logger.info(
                    "Progress: %d/%d props | %d imgs | %d errors - {source: %s}",
                    i, total, stats["images"], stats["errors"], prop["source"],
                )

    conn.close()

    logger.info(
        "DONE: %d props, %d images downloaded, %d errors",
        stats["props"], stats["images"], stats["errors"],
    )


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Download property images")
    parser.add_argument("--source", choices=VALID_SOURCES, help="Filter by source")
    parser.add_argument("--limit", type=int, help="Max properties to process")
    parser.add_argument("--new-only", action="store_true", help="Only images_downloaded=false (default behavior)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    start = time.time()
    asyncio.run(run(args))
    elapsed = time.time() - start
    logger.info("Total time: %.1fs", elapsed)


if __name__ == "__main__":
    main()

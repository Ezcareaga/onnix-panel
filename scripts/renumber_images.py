#!/usr/bin/env python3
"""Renumera las fotos ya bajadas a 1..N y sincroniza local_image_count.

Reparación one-shot del desfasaje que dejó `download_images.py` mientras nombraba
cada archivo con el índice de la URL en `image_urls`: si las primeras N URLs
fallaban, en disco quedaban `N+1.webp, N+2.webp…` y en la DB `len(urls) - N`.
El lector pide `1..local_image_count`, así que servía 404 sobre archivos que
existían — 123 URLs en 46 propiedades al 2026-08-22.

El escritor ya está arreglado (numera por descarga exitosa); esto arregla lo que
quedó escrito. Es idempotente: una segunda corrida no encuentra nada que hacer.

Uso:
    python3 scripts/renumber_images.py --dry-run           # solo reporta
    python3 scripts/renumber_images.py                     # renombra y actualiza
    python3 scripts/renumber_images.py --source remax      # un solo source
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scrapers"))

from shared.config import (  # noqa: E402
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_DB,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
)

# Misma variable que download_images.py y cleanup_images.py: las fotos son estado
# del servidor, no código (ver tests/test_script_state_paths.py).
STATE_DIR = Path(os.environ.get("ONNIX_STATE_DIR", "/home/onnix"))
IMAGES_DIR = STATE_DIR / "images"
LOG_FILE = STATE_DIR / "logs" / "renumber_images.log"
VALID_SOURCES = ("remax", "onnixpy", "coldwell", "psir")

LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [RENUMBER] %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
logger = logging.getLogger("renumber")


def setup_logging() -> None:
    handlers = [logging.StreamHandler()]
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATEFMT,
                        handlers=handlers)


# ── Renumeración (la parte pura, la que se testea) ──────────────────────────

def renumerar_dir(prop_dir: Path, dry_run: bool = False) -> tuple[int, int]:
    """Renombra las fotos de una propiedad a 1..N. Devuelve (archivos, renombrados).

    Conserva el orden relativo: la foto con el número más chico queda 1.webp. Los
    archivos que no son `<entero>.webp` se ignoran y se dejan donde están.

    El renombrado va en orden ascendente y por eso nunca pisa un nombre ocupado:
    con los números existentes ordenados, el i-ésimo es siempre >= i, así que el
    destino `i` o es el propio archivo o está libre (los menores ya se movieron).
    """
    fotos = sorted(
        (int(p.stem), p) for p in prop_dir.glob("*.webp") if p.stem.isdigit()
    )
    renombrados = 0
    for destino, (numero, path) in enumerate(fotos, 1):
        if numero == destino:
            continue
        if not dry_run:
            path.rename(prop_dir / f"{destino}.webp")
        renombrados += 1
    return len(fotos), renombrados


# ── DB ───────────────────────────────────────────────────────────────────

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402


def get_conn():
    return psycopg.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        row_factory=dict_row,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Renumera fotos a 1..N")
    parser.add_argument("--source", choices=VALID_SOURCES, help="Filtrar por source")
    parser.add_argument("--dry-run", action="store_true", help="Solo reporta, no toca nada")
    args = parser.parse_args()

    setup_logging()
    modo = "DRY-RUN" if args.dry_run else "APLICANDO"
    logger.info("%s sobre %s", modo, IMAGES_DIR)

    conn = get_conn()
    with conn.cursor() as cur:
        sql = "SELECT id, source, external_id, local_image_count FROM properties WHERE source = ANY(%s)"
        cur.execute(sql, ([args.source] if args.source else list(VALID_SOURCES),))
        props = cur.fetchall()

    stats = {"props": 0, "renumeradas": 0, "count_corregido": 0}

    for prop in props:
        prop_dir = IMAGES_DIR / prop["source"] / prop["external_id"]
        if not prop_dir.is_dir():
            continue
        stats["props"] += 1

        archivos, renombrados = renumerar_dir(prop_dir, dry_run=args.dry_run)
        if renombrados:
            stats["renumeradas"] += 1
            logger.info(
                "%s/%s: %d archivos renumerados a 1..%d",
                prop["source"], prop["external_id"], renombrados, archivos,
            )

        if (prop["local_image_count"] or 0) != archivos:
            stats["count_corregido"] += 1
            logger.info(
                "%s/%s: local_image_count %s -> %d",
                prop["source"], prop["external_id"], prop["local_image_count"], archivos,
            )
            if not args.dry_run:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE properties SET local_image_count = %s WHERE id = %s",
                        (archivos, prop["id"]),
                    )
                conn.commit()

    conn.close()
    logger.info(
        "DONE (%s): %d props con carpeta, %d renumeradas, %d con local_image_count corregido",
        modo, stats["props"], stats["renumeradas"], stats["count_corregido"],
    )


if __name__ == "__main__":
    main()

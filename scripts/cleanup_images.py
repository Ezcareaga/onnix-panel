#!/usr/bin/env python3
"""Clean up images for inactive properties and orphaned directories.

Usage:
    python3 scripts/cleanup_images.py
"""

import logging
import os
import shutil
import sys
import time
from pathlib import Path

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
LOG_FILE = STATE_DIR / "logs" / "cleanup.log"
VALID_SOURCES = ("remax", "onnixpy", "coldwell", "psir")

# ── Logging ─────────────────────────────────────────────────────────────
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [CLEANUP] %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

handlers = [logging.StreamHandler()]
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATEFMT,
                    handlers=handlers)
logger = logging.getLogger("cleanup")

# ── Database ─────────────────────────────────────────────────────────────
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


def main():
    start = time.time()
    conn = get_conn()
    stats = {"inactive_cleaned": 0, "orphans_cleaned": 0, "imgs_deleted": 0, "bytes_freed": 0}

    # ── Step 1: Clean images for inactive properties ──
    logger.info("Step 1: Cleaning images for inactive properties...")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, source, external_id
            FROM properties
            WHERE is_active = false AND images_downloaded = true
        """)
        inactive = cur.fetchall()

    for prop in inactive:
        prop_dir = IMAGES_DIR / prop["source"] / prop["external_id"]
        if prop_dir.exists():
            n_files = sum(1 for f in prop_dir.iterdir() if f.is_file())
            dir_size = sum(f.stat().st_size for f in prop_dir.iterdir() if f.is_file())
            shutil.rmtree(prop_dir)
            stats["inactive_cleaned"] += 1
            stats["imgs_deleted"] += n_files
            stats["bytes_freed"] += dir_size
            logger.info(
                "Borradas %d imagenes de %s/%s (inactiva) - %.1f KB",
                n_files, prop["source"], prop["external_id"], dir_size / 1024,
            )

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE properties SET images_downloaded = false, local_image_count = 0 WHERE id = %s",
                (prop["id"],),
            )
        conn.commit()

    # ── Step 2: Clean orphaned directories ──
    logger.info("Step 2: Scanning for orphaned directories...")
    for source in VALID_SOURCES:
        source_dir = IMAGES_DIR / source
        if not source_dir.exists():
            continue

        # Get all external_ids for this source from DB
        with conn.cursor() as cur:
            cur.execute(
                "SELECT external_id FROM properties WHERE source = %s",
                (source,),
            )
            db_ids = {row["external_id"] for row in cur.fetchall()}

        # Check each directory on disk
        for entry in source_dir.iterdir():
            if entry.is_dir() and entry.name not in db_ids:
                n_files = sum(1 for f in entry.iterdir() if f.is_file())
                dir_size = sum(f.stat().st_size for f in entry.iterdir() if f.is_file())
                shutil.rmtree(entry)
                stats["orphans_cleaned"] += 1
                stats["imgs_deleted"] += n_files
                stats["bytes_freed"] += dir_size
                logger.warning(
                    "Directorio huerfano borrado: %s/%s (%d archivos, %.1f KB)",
                    source, entry.name, n_files, dir_size / 1024,
                )

    conn.close()

    elapsed = time.time() - start
    logger.info(
        "DONE: %d inactivas, %d huerfanos, %d imgs borradas, %.1f MB liberados (%.1fs)",
        stats["inactive_cleaned"],
        stats["orphans_cleaned"],
        stats["imgs_deleted"],
        stats["bytes_freed"] / 1024 / 1024,
        elapsed,
    )


if __name__ == "__main__":
    main()

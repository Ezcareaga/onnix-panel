#!/usr/bin/env python3
"""Backfill description_embedding for properties missing embeddings.

Reads properties with description_embedding IS NULL, generates embeddings
via Gemini gemini-embedding-001, and stores them in the DB.

Usage:
    python scripts/backfill_embeddings.py [--batch-size 100] [--dry-run]
"""
import argparse
import html
import logging
import os
import re
import sys
import time

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv("/home/onnix/.env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# --- Config ---
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMS = 768
MAX_TEXT_LEN = 2000
MIN_DESC_LEN = 50  # Was 200 — lowered to reach >95% coverage (see 76-RESEARCH.md)

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", "5432")),
    "dbname": os.environ.get("POSTGRES_DB", "onnix_prod"),
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}


def strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_embedding_text(row: dict) -> str:
    """Concatenate title + city + neighborhood + description for embedding."""
    parts = []
    if row.get("title"):
        parts.append(row["title"])
    if row.get("city"):
        parts.append(row["city"])
    if row.get("neighborhood"):
        parts.append(row["neighborhood"])
    if row.get("description"):
        desc = strip_html(row["description"])
        parts.append(desc)
    text = " | ".join(parts)
    return text[:MAX_TEXT_LEN]


def should_skip(row: dict) -> bool:
    """Skip properties with insufficient text for embedding."""
    desc = row.get("description") or ""
    clean = strip_html(desc)
    title = row.get("title") or ""

    # Properties with only "Precio de venta/alquiler" or empty descriptions:
    # Allow if title is descriptive enough (>20 chars) — especially Coldwell
    if clean.strip().lower() in ("precio de venta", "precio de alquiler", ""):
        if len(title.strip()) > 20:
            return False  # Title is good enough for embedding
        return True

    if len(clean) < MIN_DESC_LEN:
        # Still allow if title compensates
        if len(title.strip()) > 20:
            return False
        return True

    return False


def fetch_candidates(conn, limit: int = 0) -> list[dict]:
    """Fetch properties needing embeddings."""
    sql = """
        SELECT id, title, city, neighborhood, description, source
        FROM properties
        WHERE description_embedding IS NULL
          AND is_active = true
        ORDER BY id
    """
    params = []
    if limit > 0:
        sql += " LIMIT %s"
        params.append(limit)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def generate_embeddings_sync(client: genai.Client, texts: list[str]) -> list[list[float]]:
    """Generate embeddings synchronously using the Gemini SDK."""
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(
            output_dimensionality=EMBEDDING_DIMS,
        ),
    )
    return [emb.values for emb in response.embeddings]


def store_embeddings(conn, updates: list[tuple[list[float], int]]) -> int:
    """Batch UPDATE description_embedding for given (vector, id) pairs."""
    sql = "UPDATE properties SET description_embedding = %s::vector WHERE id = %s"
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, updates, page_size=100)
    conn.commit()
    return len(updates)


def main():
    parser = argparse.ArgumentParser(description="Backfill property embeddings")
    parser.add_argument("--batch-size", type=int, default=100, help="Texts per API call (Gemini max=100)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and log but don't write")
    parser.add_argument("--limit", type=int, default=0, help="Max properties to process (0=all)")
    args = parser.parse_args()

    logger.info("Connecting to database...")
    conn = psycopg2.connect(**DB_CONFIG)

    logger.info("Fetching candidate properties...")
    candidates = fetch_candidates(conn, limit=args.limit)
    logger.info("Found %d candidates with description_embedding IS NULL", len(candidates))

    # Filter out properties with bad descriptions
    rows = [r for r in candidates if not should_skip(r)]
    skipped = len(candidates) - len(rows)
    logger.info("After filtering: %d to process, %d skipped (short/bad description)", len(rows), skipped)

    if not rows:
        logger.info("Nothing to do.")
        conn.close()
        return

    if args.dry_run:
        logger.info("DRY RUN — would process %d properties. Exiting.", len(rows))
        conn.close()
        return

    # Init Gemini client
    client = genai.Client(api_key=GEMINI_API_KEY)

    total_embedded = 0
    batch_size = args.batch_size
    daily_quota_hit = False

    def _is_daily_quota_error(exc: Exception) -> bool:
        """Detect Gemini daily quota exhaustion (not per-minute rate limit)."""
        return "PerDay" in str(exc) or "per_day" in str(exc)

    for batch_start in range(0, len(rows), batch_size):
        if daily_quota_hit:
            break

        batch = rows[batch_start:batch_start + batch_size]
        texts = [build_embedding_text(r) for r in batch]
        ids = [r["id"] for r in batch]

        try:
            embeddings = generate_embeddings_sync(client, texts)
        except Exception as e:
            logger.warning("Batch failed at offset %d: %s", batch_start, e)

            # Daily quota exhausted — stop immediately, no point retrying
            if _is_daily_quota_error(e):
                logger.error(
                    "Daily API quota exhausted after %d embeddings. "
                    "Will resume on next run (idempotent). Exiting.",
                    total_embedded,
                )
                daily_quota_hit = True
                break

            # Retry with longer backoff for per-minute rate limits
            for retry in range(5):
                wait = min(60 * (retry + 1), 300)  # 60s, 120s, 180s, 240s, 300s
                logger.info("Retrying in %ds...", wait)
                time.sleep(wait)
                try:
                    embeddings = generate_embeddings_sync(client, texts)
                    break
                except Exception as retry_err:
                    logger.warning("Retry %d failed: %s", retry + 1, retry_err)
                    if _is_daily_quota_error(retry_err):
                        logger.error(
                            "Daily API quota exhausted during retry after %d embeddings. "
                            "Will resume on next run. Exiting.",
                            total_embedded,
                        )
                        daily_quota_hit = True
                        break
                    continue
            else:
                logger.error("Batch at offset %d failed after 5 retries, skipping", batch_start)
                continue

            if daily_quota_hit:
                break

        # Build (vector_string, id) pairs for UPDATE
        updates = []
        for emb, pid in zip(embeddings, ids):
            vec_str = "[" + ",".join(str(v) for v in emb) + "]"
            updates.append((vec_str, pid))

        stored = store_embeddings(conn, updates)
        total_embedded += stored

        if total_embedded % 1000 < batch_size:
            logger.info("Progress: %d / %d embedded", total_embedded, len(rows))

        # Rate limit: Gemini free tier allows ~100 embed requests/min
        # With batch_size=100, sleep 62s between batches to stay within quota
        if batch_start + batch_size < len(rows):
            time.sleep(62)

    logger.info("Done! Total embedded: %d / %d", total_embedded, len(rows))
    conn.close()


if __name__ == "__main__":
    main()

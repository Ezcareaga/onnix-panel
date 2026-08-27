#!/usr/bin/env python3
"""
Genera embeddings incrementales para propiedades sin embedding.
Cron diario a las 00:30 (despues del scraper):
  30 0 * * * /usr/bin/python3 /home/onnix/scripts/generate_embeddings_incremental.py

Filtros: is_active, description >= 200 chars, source != coldwell, embedding IS NULL.
Batch de 100 via Gemini batchEmbedContents. Log a /home/onnix/logs/bot/embeddings.log.
"""
import html
import json
import logging
import re
import time
import urllib.request
import urllib.error

import psycopg2

# --- Config from .env ---
ENV = {}
for line in open("/home/onnix/.env"):
    if "=" in line and not line.startswith("#"):
        k, v = line.strip().split("=", 1)
        ENV[k] = v

GEMINI_API_KEY = ENV["GEMINI_API_KEY"]
PG_CONNSTR = (
    f"host=localhost port=5432 dbname={ENV['POSTGRES_DB']} "
    f"user={ENV['POSTGRES_USER']} password={ENV['POSTGRES_PASSWORD']}"
)
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-embedding-001:batchEmbedContents?key={GEMINI_API_KEY}"
)
BATCH_SIZE = 100
LOG_PATH = "/home/onnix/logs/bot/embeddings.log"

# --- Logging ---
logging.basicConfig(
    filename=LOG_PATH, level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("embeddings")


def clean_text(text):
    """Strip HTML, entities, non-ascii, normalize whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip()


def build_text(row):
    """title | city neighborhood | description (max 8000 chars)."""
    parts = []
    if row[1]:
        parts.append(clean_text(row[1]))
    loc = " ".join(filter(None, [row[3], row[4]]))
    if loc:
        parts.append(loc)
    if row[2]:
        parts.append(clean_text(row[2]))
    text = " | ".join(parts)
    return text[:8000] if len(text) > 8000 else text


def embed_batch(texts, retries=3):
    """Call Gemini batchEmbedContents. Returns list of 768-dim vectors."""
    body = {
        "requests": [
            {"model": "models/gemini-embedding-001",
             "content": {"parts": [{"text": t}]},
             "outputDimensionality": 768}
            for t in texts
        ]
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                GEMINI_URL, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
            return [e["values"] for e in result.get("embeddings", [])]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = (2 ** attempt) * 2
                log.warning("Rate limited, waiting %ds", wait)
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    return []


def main():
    conn = psycopg2.connect(PG_CONNSTR)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, description, city, neighborhood
        FROM properties
        WHERE is_active = true
          AND description_embedding IS NULL
          AND LENGTH(COALESCE(description, '')) >= 200
          AND source != 'coldwell'
        ORDER BY id
    """)
    rows = cur.fetchall()
    total = len(rows)
    log.info("Starting incremental embeddings: %d properties pending", total)

    if total == 0:
        log.info("Nothing to embed")
        conn.close()
        return

    embedded, errors = 0, 0
    for i in range(0, total, BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        ids, texts = [], []
        for row in batch:
            text = build_text(row)
            if len(text) >= 50:
                ids.append(row[0])
                texts.append(text)

        if not texts:
            continue

        try:
            vectors = embed_batch(texts)
            for pid, vec in zip(ids, vectors):
                vec_str = "[" + ",".join(str(v) for v in vec) + "]"
                cur.execute(
                    "UPDATE properties SET description_embedding = %s::vector WHERE id = %s",
                    (vec_str, pid),
                )
            conn.commit()
            embedded += len(vectors)
        except Exception as e:
            conn.rollback()
            errors += len(texts)
            log.error("Batch %d error: %s", i, e)

    conn.close()
    log.info("Done: embedded=%d errors=%d total=%d", embedded, errors, total)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
FASE 3C — Generate embeddings for property descriptions using Gemini.
Model: gemini-embedding-001 (768 dims, free tier: 10M tokens/min)

Usage:
    python3 scripts/generate_embeddings.py [--batch-size 100] [--limit 500] [--dry-run]
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

# Config
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or ""
if not GEMINI_API_KEY:
    for line in open("/home/onnix/.env"):
        if line.startswith("GEMINI_API_KEY="):
            GEMINI_API_KEY = line.split("=", 1)[1].strip()
            break

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents?key={GEMINI_API_KEY}"
PG_CONNSTR = "host=localhost port=5432 dbname=onnix_prod user=onnix password="
for line in open("/home/onnix/.env"):
    if line.startswith("POSTGRES_PASSWORD="):
        PG_CONNSTR += line.split("=", 1)[1].strip()
        break

# DB connection via psycopg2 or subprocess
try:
    import psycopg2
    USE_PSYCOPG2 = True
except ImportError:
    USE_PSYCOPG2 = False


def db_query(sql, params=None):
    """Execute query and return rows"""
    if USE_PSYCOPG2:
        conn = psycopg2.connect(PG_CONNSTR)
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        conn.close()
        return [dict(zip(cols, row)) for row in rows]
    else:
        # Fallback: docker exec
        import subprocess
        result = subprocess.run(
            ["docker", "exec", "onnix-postgres", "psql", "-U", "onnix",
             "-d", "onnix_prod", "-t", "-A", "-F", "\t", "-c", sql],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"psql error: {result.stderr}")
        lines = result.stdout.strip().split("\n")
        if not lines or lines == ['']:
            return []
        # Parse tab-separated output
        return [{"raw": line} for line in lines]


def db_execute(sql, params=None):
    """Execute non-query SQL"""
    if USE_PSYCOPG2:
        conn = psycopg2.connect(PG_CONNSTR)
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        conn.close()
    else:
        import subprocess
        subprocess.run(
            ["docker", "exec", "onnix-postgres", "psql", "-U", "onnix",
             "-d", "onnix_prod", "-c", sql],
            capture_output=True, text=True, check=True
        )


def clean_text(text):
    """Clean description text for embedding"""
    if not text:
        return ""
    # Strip HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode HTML entities
    text = html.unescape(text)
    # Remove bold unicode chars (common in remax listings)
    text = text.encode('ascii', 'ignore').decode('ascii')
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def prepare_embedding_text(row):
    """Build text to embed: title | city neighborhood | description"""
    parts = []
    if row.get("title"):
        parts.append(clean_text(row["title"]))
    location = " ".join(filter(None, [row.get("city"), row.get("neighborhood")]))
    if location:
        parts.append(location)
    desc = clean_text(row.get("description") or "")
    if desc:
        parts.append(desc)
    text = " | ".join(parts)
    # Truncate to ~2000 tokens (~8000 chars, conservative)
    if len(text) > 8000:
        text = text[:8000]
    return text


def embed_batch(texts, retries=3):
    """Call Gemini batch embedding API. Max 100 texts per request."""
    requests_body = {
        "requests": [
            {
                "model": "models/gemini-embedding-001",
                "content": {"parts": [{"text": t}]},
                "outputDimensionality": 768
            }
            for t in texts
        ]
    }

    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                GEMINI_URL,
                data=json.dumps(requests_body).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())

            embeddings = []
            for emb_data in result.get("embeddings", []):
                embeddings.append(emb_data["values"])
            return embeddings

        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = (2 ** attempt) * 2
                print(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            if attempt < retries - 1:
                wait = (2 ** attempt)
                print(f"  Error: {e}, retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise

    return []


def save_embeddings(id_embedding_pairs):
    """Save embeddings to database"""
    if USE_PSYCOPG2:
        conn = psycopg2.connect(PG_CONNSTR)
        cur = conn.cursor()
        for prop_id, embedding in id_embedding_pairs:
            vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
            cur.execute(
                "UPDATE properties SET description_embedding = %s::vector WHERE id = %s",
                (vec_str, prop_id)
            )
        conn.commit()
        conn.close()
    else:
        # Batch update via single SQL
        import subprocess
        sqls = []
        for prop_id, embedding in id_embedding_pairs:
            vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
            sqls.append(f"UPDATE properties SET description_embedding = '{vec_str}'::vector WHERE id = {prop_id};")
        full_sql = "\n".join(sqls)
        subprocess.run(
            ["docker", "exec", "-i", "onnix-postgres", "psql", "-U", "onnix",
             "-d", "onnix_prod"],
            input=full_sql, capture_output=True, text=True, check=True
        )


def main():
    parser = argparse.ArgumentParser(description="Generate Gemini embeddings for properties")
    parser.add_argument("--batch-size", type=int, default=100, help="Texts per API call (max 100)")
    parser.add_argument("--limit", type=int, default=0, help="Max properties to process (0=all)")
    parser.add_argument("--dry-run", action="store_true", help="Only count, don't embed")
    args = parser.parse_args()

    print("=" * 60)
    print("Gemini Embedding Pipeline — Phase 3C")
    print("=" * 60)

    # Get properties needing embeddings
    limit_clause = f"LIMIT {args.limit}" if args.limit else ""
    query = f"""
        SELECT id, title, description, city, neighborhood, source
        FROM properties
        WHERE is_active = true
        AND description_embedding IS NULL
        AND LENGTH(COALESCE(description, '')) >= 200
        AND source != 'coldwell'
        ORDER BY id
        {limit_clause}
    """

    if USE_PSYCOPG2:
        rows = db_query(query)
    else:
        # Use docker exec with JSON output
        import subprocess
        json_query = f"""
            SELECT json_agg(t) FROM (
                {query.replace(limit_clause, '')}
                {limit_clause}
            ) t
        """
        result = subprocess.run(
            ["docker", "exec", "onnix-postgres", "psql", "-U", "onnix",
             "-d", "onnix_prod", "-t", "-A", "-c", json_query],
            capture_output=True, text=True
        )
        raw = result.stdout.strip()
        rows = json.loads(raw) if raw and raw != '' else []

    total = len(rows)
    print(f"Properties to embed: {total}")

    if args.dry_run:
        # Count by source
        sources = {}
        for r in rows:
            s = r.get("source", "unknown")
            sources[s] = sources.get(s, 0) + 1
        for s, c in sorted(sources.items()):
            print(f"  {s}: {c}")
        return

    if total == 0:
        print("Nothing to embed!")
        return

    # Process in batches
    batch_size = min(args.batch_size, 100)  # Gemini max is 100 per request
    embedded = 0
    errors = 0
    skipped = 0
    start_time = time.time()

    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]

        # Prepare texts
        texts = []
        ids = []
        for row in batch:
            text = prepare_embedding_text(row)
            if len(text) < 50:
                skipped += 1
                continue
            texts.append(text)
            ids.append(row["id"])

        if not texts:
            continue

        try:
            embeddings = embed_batch(texts)
            if len(embeddings) != len(texts):
                print(f"  WARNING: got {len(embeddings)} embeddings for {len(texts)} texts")
                errors += len(texts) - len(embeddings)
                embeddings = embeddings[:len(texts)]

            pairs = list(zip(ids[:len(embeddings)], embeddings))
            save_embeddings(pairs)
            embedded += len(pairs)

        except Exception as e:
            print(f"  ERROR batch {i}: {e}")
            errors += len(texts)

        # Progress
        elapsed = time.time() - start_time
        rate = embedded / elapsed if elapsed > 0 else 0
        if (i // batch_size) % 10 == 0 or i + batch_size >= total:
            print(f"  Progress: {embedded}/{total} ({embedded*100//total}%) | "
                  f"{rate:.0f} props/s | errors: {errors} | skipped: {skipped}")

    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"DONE in {elapsed:.1f}s")
    print(f"  Embedded: {embedded}")
    print(f"  Skipped: {skipped}")
    print(f"  Errors: {errors}")
    print(f"  Rate: {embedded/elapsed:.0f} props/s" if elapsed > 0 else "")


if __name__ == "__main__":
    main()

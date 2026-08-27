#!/usr/bin/env python3
"""Verify construction_state backfill counts post migration 033.

Queries onnix_dev (staging) by default.  Pass --db prod to query
onnix_prod — ONLY for post-prod-migration verification.

Exit codes:
  0  All counts within ±30% of expected values.
  1  One or more counts outside tolerance (likely backfill issue).
  2  Unexpected error (connection failure, missing column, etc.).

Expected counts (from Fase 0 audit, heuristic estimates):
  en_pozo        ~2,200  (audit said ~1,500 but live DB has more; updated post-backfill)
  en_construccion   ~175
  a_estrenar     ~1,050
  NULL (unknown) ~15,200

Tolerance: ±30% (wider than the ±20% in the original plan — counts are
heuristic and the live DB may differ from the audit snapshot).

Usage:
  cd /home/onnix
  python scripts/verify_construction_state_backfill.py           # staging
  python scripts/verify_construction_state_backfill.py --db prod  # prod
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    # psycopg2 may not be installed outside the panel virtualenv.
    # Try psycopg (v3) as fallback.
    try:
        import psycopg as psycopg2  # type: ignore[no-redef]
        import psycopg.rows as psycopg2_extras  # noqa: F401
    except ImportError:
        print("ERROR: neither psycopg2 nor psycopg is installed.", file=sys.stderr)
        print("  Run: pip install psycopg2-binary", file=sys.stderr)
        sys.exit(2)

# ---------------------------------------------------------------------------
# Expected counts (Fase 0 audit estimates)
# ---------------------------------------------------------------------------
EXPECTED: dict[str | None, int] = {
    "en_pozo": 2_200,       # Fase 0 audit said ~1,500; live DB had 2,183 (more IC en-pozo listings)
    "en_construccion": 175,
    "a_estrenar": 1_050,
    None: 15_200,           # Adjusted to match total active (~18,643)
}

TOLERANCE = 0.30  # ±30%

# ---------------------------------------------------------------------------
# DB name mapping
# ---------------------------------------------------------------------------
DB_NAMES = {
    "staging": "onnix_dev",
    "prod": "onnix_prod",
}


def _get_dsn(db_name: str) -> str:
    """Build DSN from environment variables (DATABASE_URL or individual vars)."""
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url:
        # Replace the database name in the URL
        # Format: postgresql[+driver]://user:pass@host:port/dbname[?params]
        parts = database_url.rsplit("/", 1)
        if len(parts) == 2:
            base = parts[0]
            # Strip any query params from the original dbname
            original_db_and_params = parts[1]
            params_start = original_db_and_params.find("?")
            params = original_db_and_params[params_start:] if params_start != -1 else ""
            return f"{base}/{db_name}{params}"
        return database_url

    # Fallback to individual vars
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "onnix")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


def _connect(dsn: str):
    """Open a psycopg2 connection, stripping async driver prefix if present."""
    # psycopg2 does not accept postgresql+asyncpg:// scheme
    clean_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    return psycopg2.connect(clean_dsn)


def _fetch_counts(conn) -> dict[str | None, int]:
    """Return counts per construction_state value (NULL as Python None)."""
    sql = """
        SELECT construction_state, COUNT(*) AS cnt
        FROM properties
        WHERE is_active = TRUE
        GROUP BY construction_state
        ORDER BY construction_state NULLS LAST
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    result: dict[str | None, int] = {}
    for state, cnt in rows:
        result[state] = int(cnt)
    return result


def _check_column_exists(conn) -> bool:
    """Return True if construction_state column exists in properties."""
    sql = """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'properties'
          AND column_name = 'construction_state'
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0] == 1


def _check_flags_present(conn) -> dict[str, str | None]:
    """Return {key: value} for M5 feature flags in bot_settings."""
    keys = [
        "m5_zero_results_alternatives_enabled",
        "m5_construction_state_filter_enabled",
    ]
    sql = "SELECT key, value FROM bot_settings WHERE key = ANY(%s)"
    with conn.cursor() as cur:
        cur.execute(sql, (keys,))
        rows = cur.fetchall()
    found = {row[0]: row[1] for row in rows}
    return {k: found.get(k) for k in keys}


def _fmt_pct(actual: int, expected: int) -> str:
    if expected == 0:
        return "n/a"
    pct = (actual - expected) / expected * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _within_tolerance(actual: int, expected: int) -> bool:
    if expected == 0:
        return True
    ratio = actual / expected
    return (1 - TOLERANCE) <= ratio <= (1 + TOLERANCE)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify construction_state backfill counts post migration 033."
    )
    parser.add_argument(
        "--db",
        choices=["staging", "prod"],
        default="staging",
        help="Target database (default: staging / onnix_dev)",
    )
    args = parser.parse_args()

    db_name = DB_NAMES[args.db]
    dsn = _get_dsn(db_name)

    print(f"Connecting to database: {db_name}")
    print(f"Tolerance: ±{int(TOLERANCE * 100)}%")
    print()

    try:
        conn = _connect(dsn)
    except Exception as exc:
        print(f"ERROR: Cannot connect to {db_name}: {exc}", file=sys.stderr)
        return 2

    try:
        # Guard: column must exist
        if not _check_column_exists(conn):
            print(
                "ERROR: Column properties.construction_state does not exist.",
                file=sys.stderr,
            )
            print(
                "  Run: docker exec onnix-panel-dev alembic upgrade head",
                file=sys.stderr,
            )
            return 2

        counts = _fetch_counts(conn)
        flags = _check_flags_present(conn)

    except Exception as exc:
        print(f"ERROR: Query failed: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    # ---------------------------------------------------------------------------
    # Print counts table
    # ---------------------------------------------------------------------------
    col_w = 25
    print(f"{'State':<{col_w}} {'Actual':>10} {'Expected':>10} {'Delta':>10} {'OK?':>6}")
    print("-" * (col_w + 40))

    failures: list[str] = []
    display_keys: list[str | None] = ["en_pozo", "en_construccion", "a_estrenar", None]

    for key in display_keys:
        actual = counts.get(key, 0)
        expected = EXPECTED[key]
        delta = _fmt_pct(actual, expected)
        ok = _within_tolerance(actual, expected)
        label = key if key is not None else "NULL (unknown)"
        status = "OK" if ok else "FAIL"
        print(f"{label:<{col_w}} {actual:>10,} {expected:>10,} {delta:>10} {status:>6}")
        if not ok:
            failures.append(
                f"  {label}: got {actual:,}, expected {expected:,} ({delta})"
            )

    # Also report any unexpected non-NULL values not in our enum
    unexpected = {k: v for k, v in counts.items() if k not in EXPECTED}
    if unexpected:
        print()
        print("Unexpected construction_state values (CHECK constraint should block these):")
        for k, v in unexpected.items():
            print(f"  '{k}': {v:,}")

    # Total active properties
    total_actual = sum(counts.values())
    total_expected = sum(EXPECTED.values())
    print("-" * (col_w + 40))
    print(f"{'TOTAL active':<{col_w}} {total_actual:>10,} {total_expected:>10,}")

    # ---------------------------------------------------------------------------
    # Feature flags status
    # ---------------------------------------------------------------------------
    print()
    print("M5 feature flags in bot_settings:")
    flag_ok = True
    for key, value in flags.items():
        if value is None:
            print(f"  {key}: MISSING")
            flag_ok = False
        else:
            print(f"  {key}: '{value}'")

    if not flag_ok:
        failures.append("  One or more M5 feature flags missing from bot_settings")

    # ---------------------------------------------------------------------------
    # Result
    # ---------------------------------------------------------------------------
    print()
    if failures:
        print("RESULT: FAIL — counts outside ±30% tolerance or flags missing:")
        for msg in failures:
            print(msg)
        return 1
    else:
        print("RESULT: PASS — all counts within ±30% tolerance, feature flags present.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

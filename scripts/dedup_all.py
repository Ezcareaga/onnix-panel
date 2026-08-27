#!/usr/bin/env python3
"""
Onnix SA -- Cross-source deduplication orchestrator.

CLI wrapper around shared.dedup.run_dedup_all() and run_dedup_for_source().
Satisfies requirements DEDUP-01 through DEDUP-04.

Usage:
    python scripts/dedup_all.py                     # Full dedup across all sources
    python scripts/dedup_all.py --source remax      # Dedup only remax
    python scripts/dedup_all.py --dry-run            # Report duplicates without writing
    python scripts/dedup_all.py --batch-since 2026-02-23T00:00:00  # Incremental
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# Add scrapers/ to sys.path for shared module imports
_scrapers_dir = str(Path(__file__).resolve().parent.parent / "scrapers")
if _scrapers_dir not in sys.path:
    sys.path.insert(0, _scrapers_dir)

from shared.config import setup_logging
from shared.db import get_connection
from shared.dedup import (
    run_dedup_all,
    run_dedup_for_source,
    run_dedup_same_source,
    SOURCE_PRIORITY,
)

logger = logging.getLogger("dedup_all")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Cross-source duplicate detection for properties."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report duplicates without writing to database.",
    )
    parser.add_argument(
        "--source",
        type=str,
        choices=list(SOURCE_PRIORITY.keys()),
        help="Run dedup for a single source only.",
    )
    parser.add_argument(
        "--batch-since",
        type=str,
        default=None,
        help="Only process properties scraped since this ISO timestamp (e.g. 2026-02-23T00:00:00).",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO).",
    )
    return parser.parse_args()


def get_total_property_count() -> int:
    """Return total number of property rows (active + inactive, including duplicates)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM properties")
            return cur.fetchone()[0]


def get_duplicate_count() -> int:
    """Return number of properties currently marked as duplicates."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM properties WHERE duplicate_of IS NOT NULL")
            return cur.fetchone()[0]


def get_duplicate_stats() -> dict:
    """Return duplicate counts grouped by source."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT source, count(*) as dup_count
                FROM properties
                WHERE duplicate_of IS NOT NULL
                GROUP BY source
                ORDER BY count(*) DESC
            """)
            return {row[0]: row[1] for row in cur.fetchall()}


def main() -> int:
    """Main entry point. Returns exit code (0=success, 1=error)."""
    args = parse_args()
    setup_logging(args.log_level)

    logger.info(
        "Starting dedup - %s",
        {"dry_run": args.dry_run, "source": args.source,
         "batch_since": args.batch_since},
    )

    # Record count BEFORE dedup (DEDUP-03: no rows ever deleted)
    count_before = get_total_property_count()
    dupes_before = get_duplicate_count()
    logger.info(
        "State before dedup - %s",
        {"total_properties": count_before, "existing_duplicates": dupes_before},
    )

    start_time = time.time()

    try:
        if args.source:
            # Mismo orden que run_dedup_all: primero adentro del portal.
            same = run_dedup_same_source(
                source=args.source, dry_run=args.dry_run
            )
            stats = run_dedup_for_source(
                source=args.source,
                batch_since=args.batch_since,
                dry_run=args.dry_run,
            )
            results = {"__misma_fuente__": same, args.source: stats}
        else:
            results = run_dedup_all(dry_run=args.dry_run)
    except Exception:
        logger.exception("Dedup failed with error")
        return 1

    elapsed = time.time() - start_time

    # Record count AFTER dedup (DEDUP-03: verify no rows deleted)
    count_after = get_total_property_count()
    dupes_after = get_duplicate_count()

    if count_before != count_after:
        logger.critical(
            "ROW COUNT CHANGED DURING DEDUP! before=%d after=%d",
            count_before, count_after,
        )
        return 1

    # Print summary
    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{'=' * 60}")
    print(f"{prefix}Deduplication Summary")
    print(f"{'=' * 60}")
    print(f"Elapsed time:        {elapsed:.1f}s")
    print(f"Total properties:    {count_after} (unchanged)")
    print(f"Duplicates before:   {dupes_before}")
    print(f"Duplicates after:    {dupes_after}")
    print(f"New duplicates:      {dupes_after - dupes_before}")
    print()

    total_analyzed = 0
    total_found = 0
    total_marked = 0
    same = results.pop("__misma_fuente__", None)
    if same is not None:
        print("  dentro de cada portal (titulo + precio + superficie):")
        print(f"    Grupos:      {same.get('grupos', 0)}")
        print(f"    Marcadas:    {same.get('marcadas', 0)}")
        total_marked += same.get("marcadas", 0)

    for source_name, source_stats in results.items():
        print(f"  {source_name}:")
        print(f"    Analyzed:    {source_stats.get('analyzed', 0)}")
        print(f"    Found:       {source_stats.get('duplicates_found', 0)}")
        print(f"    Marked:      {source_stats.get('duplicates_marked', 0)}")
        total_analyzed += source_stats.get('analyzed', 0)
        total_found += source_stats.get('duplicates_found', 0)
        total_marked += source_stats.get('duplicates_marked', 0)

    # Show per-source duplicate distribution
    if not args.dry_run:
        dup_stats = get_duplicate_stats()
        if dup_stats:
            print(f"\nDuplicates by source:")
            for src, cnt in dup_stats.items():
                print(f"  {src}: {cnt}")

    print(f"{'=' * 60}\n")

    logger.info(
        "Dedup complete - %s",
        {"elapsed_s": round(elapsed, 1), "new_duplicates": dupes_after - dupes_before,
         "total_properties": count_after},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

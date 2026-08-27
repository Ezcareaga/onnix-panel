#!/usr/bin/env python3
"""
Onnix SA -- Exchange rate updater and price recalculator.

Fetches the daily USD/PYG exchange rate using a 3-level fallback chain,
stores it in the database, and recalculates property prices if the rate
changed by more than 2%.

Satisfies requirements DEDUP-05, DEDUP-06, DEDUP-07.

Fallback chain (DEDUP-07):
    1. open.er-api.com (primary free API)
    2. fawazahmed0/currency-api (CDN fallback)
    3. Most recent rate from exchange_rates table (DB fallback + notes)

Usage:
    python scripts/update_exchange_rate.py                # Normal run
    python scripts/update_exchange_rate.py --dry-run      # Fetch and report without saving
    python scripts/update_exchange_rate.py --force         # Force price recalculation regardless of threshold
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
from shared.currency import fetch_exchange_rate, recalculate_prices
from shared.db import get_latest_exchange_rate, store_exchange_rate

logger = logging.getLogger("update_exchange_rate")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Fetch daily exchange rate and recalculate property prices."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch rate and report without saving to database or recalculating prices.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force price recalculation even if rate change is <= 2%%.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO).",
    )
    return parser.parse_args()


def main() -> int:
    """Main entry point. Returns exit code (0=success, 1=error)."""
    args = parse_args()
    setup_logging(args.log_level)

    logger.info(
        "Starting exchange rate update - %s",
        {"dry_run": args.dry_run, "force": args.force},
    )

    start_time = time.time()

    # Step 1: Get the previous rate for comparison
    prev = get_latest_exchange_rate()
    prev_rate = prev[0] if prev else None
    prev_date = prev[1] if prev else None

    # Step 2: Fetch the current rate (3-level fallback chain)
    try:
        rate, source = fetch_exchange_rate()
    except RuntimeError as e:
        logger.critical("All exchange rate sources failed - %s", {"error": str(e)})
        print(f"CRITICAL: {e}")
        return 1

    # Step 3: Determine notes for DEDUP-07 (fallback annotation)
    notes = None
    if source == "db-fallback":
        notes = "fallback: yesterday's rate (both APIs unreachable)"
        logger.warning(
            "Using DB fallback rate - %s",
            {"rate": str(rate), "notes": notes},
        )

    # Step 4: Calculate rate change percentage
    change_pct = 0.0
    if prev_rate and prev_rate > 0:
        change_pct = abs(float(rate - prev_rate)) / float(prev_rate) * 100

    # Step 5: Store the rate (unless dry-run)
    if not args.dry_run:
        store_exchange_rate(rate, source=source, notes=notes)
        logger.info(
            "Exchange rate stored - %s",
            {"rate": str(rate), "source": source, "notes": notes},
        )
    else:
        logger.info(
            "DRY RUN: would store rate - %s",
            {"rate": str(rate), "source": source, "notes": notes},
        )

    # Step 6: Recalculate prices if rate changed significantly (or --force)
    recalc_result = {"updated_usd": 0, "updated_pyg": 0, "skipped": True,
                     "reason": "dry-run mode"}

    if not args.dry_run:
        recalc_result = recalculate_prices(rate)

    elapsed = time.time() - start_time

    # Print summary
    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{'=' * 60}")
    print(f"{prefix}Exchange Rate Update Summary")
    print(f"{'=' * 60}")
    print(f"Elapsed time:        {elapsed:.1f}s")
    print(f"Rate source:         {source}")
    print(f"Rate (USD->PYG):     {rate}")
    if prev_rate:
        print(f"Previous rate:       {prev_rate} ({prev_date})")
        print(f"Change:              {change_pct:.2f}%")
    else:
        print(f"Previous rate:       (none)")
    if notes:
        print(f"Notes:               {notes}")
    print()
    print(f"Price recalculation:")
    if recalc_result["skipped"]:
        print(f"  Status:            Skipped ({recalc_result.get('reason', 'N/A')})")
    else:
        print(f"  USD->PYG updated:  {recalc_result['updated_pyg']} properties")
        print(f"  PYG->USD updated:  {recalc_result['updated_usd']} properties")
    print(f"{'=' * 60}\n")

    logger.info(
        "Exchange rate update complete - %s",
        {"elapsed_s": round(elapsed, 1), "rate": str(rate), "source": source,
         "change_pct": round(change_pct, 2),
         "recalc_skipped": recalc_result["skipped"]},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

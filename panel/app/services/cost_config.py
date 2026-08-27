"""AI provider pricing constants — USD per 1M tokens.

Sources (verified 2026-04-18):
- Claude Haiku 4.5: https://www.anthropic.com/pricing
  (claude-haiku-4-5: $1.00/M input, $5.00/M output)
  Cache write: 1.25x input rate ($1.25/M); cache read: 0.10x input rate ($0.10/M)
- Gemini 3 Flash:   https://ai.google.dev/pricing
  (gemini-2.0-flash / gemini-3-flash: $0.30/M input, $2.50/M output)

Override via env vars if Anthropic/Google change pricing between redeploys:
  CLAUDE_HAIKU_INPUT_USD_PER_M   (default "1.00")
  CLAUDE_HAIKU_OUTPUT_USD_PER_M  (default "5.00")
  GEMINI_FLASH_INPUT_USD_PER_M   (default "0.30")
  GEMINI_FLASH_OUTPUT_USD_PER_M  (default "2.50")

Unknown model names (neither claude* nor gemini*) fall back to Claude Haiku
rates — conservative overage is preferable to silent undercount.
"""
from __future__ import annotations

import os
from decimal import Decimal


def _env_decimal(key: str, default: str) -> Decimal:
    """Read an env var as Decimal; fall back to *default* if absent or blank."""
    val = os.getenv(key, "").strip()
    return Decimal(val) if val else Decimal(default)


# ---------------------------------------------------------------------------
# Rate constants (USD per 1 million tokens)
# ---------------------------------------------------------------------------

CLAUDE_HAIKU_INPUT: Decimal = _env_decimal("CLAUDE_HAIKU_INPUT_USD_PER_M", "1.00")
CLAUDE_HAIKU_OUTPUT: Decimal = _env_decimal("CLAUDE_HAIKU_OUTPUT_USD_PER_M", "5.00")
GEMINI_FLASH_INPUT: Decimal = _env_decimal("GEMINI_FLASH_INPUT_USD_PER_M", "0.30")
GEMINI_FLASH_OUTPUT: Decimal = _env_decimal("GEMINI_FLASH_OUTPUT_USD_PER_M", "2.50")

# Prompt cache rates for Claude (Anthropic billing):
#   cache_creation: 1.25x the normal input rate
#   cache_read:     0.10x the normal input rate
CLAUDE_HAIKU_CACHE_WRITE: Decimal = CLAUDE_HAIKU_INPUT * Decimal("1.25")
CLAUDE_HAIKU_CACHE_READ: Decimal = CLAUDE_HAIKU_INPUT * Decimal("0.10")

_MILLION = Decimal(1_000_000)


def compute_cost_usd(
    model: str | None,
    tokens_in: int | None,
    tokens_out: int | None,
    cache_creation_in: int = 0,
    cache_read_in: int = 0,
) -> Decimal:
    """Return USD cost for a single AI API call.

    Returns ``Decimal('0')`` when *model* is missing/None or when either
    base token count is None.  Unknown model names default to Claude Haiku rates.

    Cache token arguments are optional (default 0) so existing callers without
    cache instrumentation continue to work unchanged.

    Args:
        model:             Model string (e.g. "claude-haiku-4-5", "gemini-3-flash").
        tokens_in:         Regular input tokens billed at the base input rate.
        tokens_out:        Output tokens.
        cache_creation_in: Tokens written to the prompt cache (1.25x input rate).
        cache_read_in:     Tokens read from the prompt cache (0.10x input rate).
    """
    if not model or tokens_in is None or tokens_out is None:
        return Decimal("0")

    model_lower = model.lower()
    if model_lower.startswith("gemini"):
        in_rate, out_rate = GEMINI_FLASH_INPUT, GEMINI_FLASH_OUTPUT
        # Gemini does not currently have prompt-cache billing via this path
        cache_write_rate = GEMINI_FLASH_INPUT
        cache_read_rate = GEMINI_FLASH_INPUT
    else:
        # Default: Claude Haiku — covers haiku-4-5, haiku-4-5-20251001, etc.
        in_rate, out_rate = CLAUDE_HAIKU_INPUT, CLAUDE_HAIKU_OUTPUT
        cache_write_rate = CLAUDE_HAIKU_CACHE_WRITE
        cache_read_rate = CLAUDE_HAIKU_CACHE_READ

    total = (
        Decimal(tokens_in) * in_rate
        + Decimal(tokens_out) * out_rate
        + Decimal(cache_creation_in) * cache_write_rate
        + Decimal(cache_read_in) * cache_read_rate
    )
    return total / _MILLION

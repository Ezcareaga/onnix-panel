"""Tests for app/services/cost_config.py — pricing constants and compute_cost_usd."""
from __future__ import annotations

import importlib
from decimal import Decimal

import pytest


# ---------------------------------------------------------------------------
# compute_cost_usd — model routing
# ---------------------------------------------------------------------------

class TestComputeCostUsdModelRouting:

    def test_gemini_model_uses_gemini_rates(self):
        """Model starting with 'gemini' uses GEMINI_FLASH rates."""
        from app.services.cost_config import (
            GEMINI_FLASH_INPUT,
            GEMINI_FLASH_OUTPUT,
            compute_cost_usd,
        )
        cost = compute_cost_usd("gemini-3-flash", 1_000_000, 1_000_000)
        expected = GEMINI_FLASH_INPUT + GEMINI_FLASH_OUTPUT
        assert cost == expected

    def test_gemini_model_case_insensitive(self):
        """Model prefix matching is case-insensitive."""
        from app.services.cost_config import (
            GEMINI_FLASH_INPUT,
            GEMINI_FLASH_OUTPUT,
            compute_cost_usd,
        )
        cost = compute_cost_usd("GEMINI-2.0-flash", 1_000_000, 1_000_000)
        expected = GEMINI_FLASH_INPUT + GEMINI_FLASH_OUTPUT
        assert cost == expected

    def test_claude_haiku_uses_claude_rates(self):
        """Model 'claude-haiku-4-5' uses CLAUDE_HAIKU rates."""
        from app.services.cost_config import (
            CLAUDE_HAIKU_INPUT,
            CLAUDE_HAIKU_OUTPUT,
            compute_cost_usd,
        )
        cost = compute_cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000)
        expected = CLAUDE_HAIKU_INPUT + CLAUDE_HAIKU_OUTPUT
        assert cost == expected

    def test_claude_haiku_versioned_uses_claude_rates(self):
        """Model 'claude-haiku-4-5-20251001' also uses CLAUDE_HAIKU rates."""
        from app.services.cost_config import (
            CLAUDE_HAIKU_INPUT,
            CLAUDE_HAIKU_OUTPUT,
            compute_cost_usd,
        )
        cost = compute_cost_usd("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
        expected = CLAUDE_HAIKU_INPUT + CLAUDE_HAIKU_OUTPUT
        assert cost == expected

    def test_unknown_model_defaults_to_claude_rates(self):
        """Unrecognised model falls through to Claude Haiku rates (conservative overage)."""
        from app.services.cost_config import (
            CLAUDE_HAIKU_INPUT,
            CLAUDE_HAIKU_OUTPUT,
            compute_cost_usd,
        )
        cost = compute_cost_usd("gpt-4o-mini", 1_000_000, 1_000_000)
        expected = CLAUDE_HAIKU_INPUT + CLAUDE_HAIKU_OUTPUT
        assert cost == expected


# ---------------------------------------------------------------------------
# compute_cost_usd — missing / None inputs
# ---------------------------------------------------------------------------

class TestComputeCostUsdMissingInputs:

    def test_none_model_returns_zero(self):
        from app.services.cost_config import compute_cost_usd
        assert compute_cost_usd(None, 100, 200) == Decimal("0")

    def test_empty_model_returns_zero(self):
        from app.services.cost_config import compute_cost_usd
        assert compute_cost_usd("", 100, 200) == Decimal("0")

    def test_none_tokens_in_returns_zero(self):
        from app.services.cost_config import compute_cost_usd
        assert compute_cost_usd("claude-haiku-4-5", None, 200) == Decimal("0")

    def test_none_tokens_out_returns_zero(self):
        from app.services.cost_config import compute_cost_usd
        assert compute_cost_usd("claude-haiku-4-5", 100, None) == Decimal("0")

    def test_both_tokens_none_returns_zero(self):
        from app.services.cost_config import compute_cost_usd
        assert compute_cost_usd("gemini-3-flash", None, None) == Decimal("0")


# ---------------------------------------------------------------------------
# compute_cost_usd — return type and precision
# ---------------------------------------------------------------------------

class TestComputeCostUsdReturnType:

    def test_returns_decimal_not_float(self):
        """Result must be Decimal for money precision."""
        from app.services.cost_config import compute_cost_usd
        result = compute_cost_usd("claude-haiku-4-5", 500, 300)
        assert isinstance(result, Decimal), f"Expected Decimal, got {type(result)}"

    def test_math_is_correct_for_known_values(self):
        """1000 tokens in + 1000 tokens out at Claude default rates.

        $1.00/M in + $5.00/M out:
        (1000 * 1.00 + 1000 * 5.00) / 1_000_000 = 6000 / 1_000_000 = 0.006
        """
        from app.services.cost_config import compute_cost_usd
        cost = compute_cost_usd("claude-haiku-4-5", 1000, 1000)
        assert cost == Decimal("0.006")

    def test_zero_tokens_returns_zero(self):
        """Zero token counts (not None) produce a zero cost."""
        from app.services.cost_config import compute_cost_usd
        assert compute_cost_usd("claude-haiku-4-5", 0, 0) == Decimal("0")


# ---------------------------------------------------------------------------
# Env override
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# compute_cost_usd — cache tokens
# ---------------------------------------------------------------------------

class TestComputeCostUsdCacheTokens:

    def test_cache_creation_tokens_billed_at_1_25x_input(self):
        """cache_creation_in tokens are billed at 1.25x the input rate.

        At default Claude rates ($1.00/M in):
        cache_creation_rate = $1.25/M
        1M cache_creation_in = $1.25
        """
        from app.services.cost_config import CLAUDE_HAIKU_CACHE_WRITE, compute_cost_usd

        cost = compute_cost_usd("claude-haiku-4-5", 0, 0, cache_creation_in=1_000_000)
        expected = CLAUDE_HAIKU_CACHE_WRITE  # $1.25
        assert cost == expected

    def test_cache_read_tokens_billed_at_0_10x_input(self):
        """cache_read_in tokens are billed at 0.10x the input rate.

        At default Claude rates ($1.00/M in):
        cache_read_rate = $0.10/M
        1M cache_read_in = $0.10
        """
        from app.services.cost_config import CLAUDE_HAIKU_CACHE_READ, compute_cost_usd

        cost = compute_cost_usd("claude-haiku-4-5", 0, 0, cache_read_in=1_000_000)
        expected = CLAUDE_HAIKU_CACHE_READ  # $0.10
        assert cost == expected

    def test_combined_all_token_types(self):
        """All four token types are summed correctly.

        1M in at $1.00 + 1M out at $5.00 + 1M cache_create at $1.25 + 1M cache_read at $0.10 = $7.35
        """
        from app.services.cost_config import compute_cost_usd
        from decimal import Decimal

        cost = compute_cost_usd(
            "claude-haiku-4-5",
            1_000_000,
            1_000_000,
            cache_creation_in=1_000_000,
            cache_read_in=1_000_000,
        )
        assert cost == Decimal("7.35")

    def test_cache_tokens_default_to_zero(self):
        """Omitting cache_creation_in/cache_read_in gives same result as passing 0."""
        from app.services.cost_config import compute_cost_usd

        without_cache = compute_cost_usd("claude-haiku-4-5", 1000, 1000)
        with_zero_cache = compute_cost_usd(
            "claude-haiku-4-5", 1000, 1000, cache_creation_in=0, cache_read_in=0
        )
        assert without_cache == with_zero_cache


class TestEnvOverride:

    def test_env_override_changes_rate(self, monkeypatch):
        """Setting CLAUDE_HAIKU_INPUT_USD_PER_M changes the computed rate."""
        monkeypatch.setenv("CLAUDE_HAIKU_INPUT_USD_PER_M", "2.00")
        monkeypatch.setenv("CLAUDE_HAIKU_OUTPUT_USD_PER_M", "10.00")

        # Force module reload to pick up new env values
        import app.services.cost_config as mod
        importlib.reload(mod)

        # 1M in at $2 + 1M out at $10 = $12
        cost = mod.compute_cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000)
        assert cost == Decimal("12")

        # Restore to defaults by reloading without the env vars
        monkeypatch.delenv("CLAUDE_HAIKU_INPUT_USD_PER_M", raising=False)
        monkeypatch.delenv("CLAUDE_HAIKU_OUTPUT_USD_PER_M", raising=False)
        importlib.reload(mod)

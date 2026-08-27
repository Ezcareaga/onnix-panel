"""Tests for team_mentions helper + migration 031 seed.

M2.F6: generic team labels live in bot_settings. This test file is the
permanent guard that prevents proper names from sneaking back into defaults.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.bot.services.team_mentions import _DEFAULTS, get_team_mention


class TestGetTeamMention:
    """get_team_mention reads bot_settings with safe defaults."""

    @pytest.mark.asyncio
    async def test_singular_from_db(self):
        """Stored value is returned when present."""
        session = AsyncMock()
        with patch(
            "app.bot.services.team_mentions.BotSettingRepository.get_value",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = "un ejecutivo"
            result = await get_team_mention(session, "singular")
        assert result == "un ejecutivo"
        mock_get.assert_awaited_once_with(session, "team_mention_singular")

    @pytest.mark.asyncio
    async def test_collective_from_db(self):
        session = AsyncMock()
        with patch(
            "app.bot.services.team_mentions.BotSettingRepository.get_value",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = "la inmobiliaria"
            result = await get_team_mention(session, "collective")
        assert result == "la inmobiliaria"
        mock_get.assert_awaited_once_with(session, "team_mention_collective")

    @pytest.mark.asyncio
    async def test_singular_fallback_when_none(self):
        """Missing row → falls back to generic default."""
        session = AsyncMock()
        with patch(
            "app.bot.services.team_mentions.BotSettingRepository.get_value",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = None
            result = await get_team_mention(session, "singular")
        assert result == "un asesor"

    @pytest.mark.asyncio
    async def test_collective_fallback_when_empty(self):
        """Empty string → falls back to default (treated like missing)."""
        session = AsyncMock()
        with patch(
            "app.bot.services.team_mentions.BotSettingRepository.get_value",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = ""
            result = await get_team_mention(session, "collective")
        assert result == "el equipo comercial"

    @pytest.mark.asyncio
    async def test_invalid_kind_raises(self):
        session = AsyncMock()
        with pytest.raises(ValueError, match="Invalid mention kind"):
            await get_team_mention(session, "foo")  # type: ignore[arg-type]


class TestDefaultsAreGeneric:
    """Permanent guard: defaults must NEVER contain proper names."""

    # Los nombres propios del equipo de ESTE deployment. La lista llego del
    # panel del que se forkeo con los nombres del cliente viejo adentro; se
    # vaciaron al forkear. Queda `ez`/`careaga`, que sigue siendo real, y hay
    # que sumarle los del equipo de Onnix cuando se sepan — con la lista vacia
    # el test es decorativo.
    _FORBIDDEN_NAMES = {
        "ez", "careaga",
    }

    def test_no_proper_names_in_defaults(self):
        for kind, text in _DEFAULTS.items():
            low = text.lower()
            for name in self._FORBIDDEN_NAMES:
                assert name not in low, (
                    f"Default for {kind!r} contains forbidden name "
                    f"{name!r}: {text!r}"
                )

    def test_singular_is_generic_role(self):
        assert _DEFAULTS["singular"] == "un asesor"

    def test_collective_is_generic_group(self):
        assert _DEFAULTS["collective"] == "el equipo comercial"


def _load_migration_031():
    """Load the migration module by absolute file path.

    alembic/versions/ is not a Python package (no __init__.py), so regular
    import syntax does not work. Use importlib.util.spec_from_file_location
    to load the file directly.
    """
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic" / "versions" / "031_seed_team_mentions.py"
    )
    spec = importlib.util.spec_from_file_location("m031_team_mentions", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigration031SeedStructure:
    """Guard the migration 031 seed values match the helper defaults."""

    def test_migration_seeds_match_helper_defaults(self):
        """Drift protection: if someone changes one without the other."""
        mig = _load_migration_031()
        seeds = {k: v[0] for k, v in mig._DEFAULTS.items()}

        assert seeds["team_mention_singular"] == _DEFAULTS["singular"]
        assert seeds["team_mention_collective"] == _DEFAULTS["collective"]

    def test_migration_seeds_have_no_proper_names(self):
        """Double check the migration SQL can't leak names either."""
        mig = _load_migration_031()
        forbidden = {"ez", "careaga"}
        for key, (value, description) in mig._DEFAULTS.items():
            for name in forbidden:
                assert name not in value.lower(), (
                    f"Migration seed value for {key!r} contains {name!r}"
                )
                assert name not in description.lower(), (
                    f"Migration seed description for {key!r} contains {name!r}"
                )

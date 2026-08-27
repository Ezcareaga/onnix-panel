"""Team mention helper — reads generic team labels from bot_settings.

M2.F6: the bot never hardcodes proper names. When referring to the commercial
team it uses generic roles ("un asesor" / "el equipo comercial"), which come
from bot_settings so the operator can edit them without a code change. A
restart of the panel container is required for the new values to take effect
in the cached system prompt.

The helper returns safe genereric defaults when:
  - the row is missing (migration not yet applied on this environment)
  - the stored value is empty / NULL

Defaults are ALWAYS generic roles — never proper names.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from app.repositories.bot_setting_repo import BotSettingRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


MentionKind = Literal["singular", "collective"]


# Module-level defaults — single source of truth for both the helper and the
# tests that guard against accidental name leaks.
_DEFAULTS: dict[str, str] = {
    "singular": "un asesor",
    "collective": "el equipo comercial",
}


async def get_team_mention(
    session: "AsyncSession",
    kind: MentionKind = "singular",
) -> str:
    """Return the generic team mention for the given kind.

    Parameters
    ----------
    session : AsyncSession
        Request-scoped SQLAlchemy session.
    kind : "singular" | "collective"
        Which variant to retrieve. Singular is used when the bot refers to
        one advisor ("un asesor"); collective when referring to the team as
        a group ("el equipo comercial").

    Returns
    -------
    str
        The stored value, or the module-level default when missing/empty.
        Never returns a proper name — the defaults are enforced at write
        time via the migration (see alembic 031_seed_team_mentions.py) and
        by the test guard in test_team_mentions.py.

    Raises
    ------
    ValueError
        If ``kind`` is not one of ``"singular"`` or ``"collective"``.
    """
    if kind not in _DEFAULTS:
        raise ValueError(
            f"Invalid mention kind: {kind!r}. Use 'singular' or 'collective'."
        )

    key = f"team_mention_{kind}"
    value = await BotSettingRepository.get_value(session, key)
    return value or _DEFAULTS[kind]

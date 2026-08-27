"""Seed team_mention_* keys into bot_settings (M2.F6).

Revision ID: 031
Revises: 030_anthropic_api_calls
Create Date: 2026-04-18

Inserts the generic team mentions that the bot uses when referring to the
commercial team. la administradora can edit these values from the panel at any time
and, after a `docker compose restart onnix-panel`, the new values apply across
all bot templates and the system prompt.

Rule: these values are ALWAYS generic roles — never proper names. The bot's
identity is "Onnix" and the commercial team is referenced as a group, not
by individuals. This prevents text drift when the team grows or turns over.

Keys seeded:
  - team_mention_singular   → default "un asesor"
  - team_mention_collective → default "el equipo comercial"

Both use INSERT ... ON CONFLICT DO NOTHING so re-running the migration is
safe and does NOT overwrite values an operator may have changed already.
The downgrade removes both rows.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "031"
down_revision = "030_anthropic_api_calls"
branch_labels = None
depends_on = None


# Exposed at module level so tests can import without duplicating SQL.
_DEFAULTS: dict[str, tuple[str, str]] = {
    # key: (value, description)
    "team_mention_singular": (
        "un asesor",
        "Texto genérico usado por el bot cuando menciona un asesor del equipo "
        "en singular. Sin nombres propios.",
    ),
    "team_mention_collective": (
        "el equipo comercial",
        "Texto genérico usado por el bot cuando menciona al equipo en conjunto. "
        "Sin nombres propios.",
    ),
}

_UPGRADE_SQL: str = (
    "INSERT INTO bot_settings (key, value, description, updated_at) "
    "VALUES (:key, :value, :description, NOW()) "
    "ON CONFLICT (key) DO NOTHING"
)

_DOWNGRADE_SQL: str = "DELETE FROM bot_settings WHERE key = :key"


def upgrade() -> None:
    bind = op.get_bind()
    for key, (value, description) in _DEFAULTS.items():
        bind.execute(
            sa.text(_UPGRADE_SQL),
            {"key": key, "value": value, "description": description},
        )


def downgrade() -> None:
    bind = op.get_bind()
    for key in _DEFAULTS:
        bind.execute(sa.text(_DOWNGRADE_SQL), {"key": key})

"""Seed bot_default_mode into bot_settings (M6.3 / Phase 123-01).

Revision ID: 041_seed_bot_default_mode
Revises: 040_visits
Create Date: 2026-06-07

BOT-01 — the global bot mode switch.

`bot_settings` is a key/value table (key VARCHAR(50) PK, value TEXT), NOT a wide
typed-column table, so BOT-01's literal `VARCHAR(20) CHECK IN (...)` is realized
as a ROW whose allowed values {'recepcionista','busqueda'} are enforced at the
SERVICE layer (SettingsService.set_bot_default_mode), not a column CHECK.
See .planning/phases/122-m6.3-plan-bot-recepcionista/122-01-PLAN.md §2.

Default is 'busqueda' (BOT-01): deploying the recepcionista must NOT auto-switch
live conversations. The flip to 'recepcionista' is a deliberate post-merge admin
action (Phase 124).

Uses INSERT ... ON CONFLICT (key) DO NOTHING so re-running is safe and does NOT
overwrite a value an operator may have changed from the panel — critical: it
MUST NOT reset prod to 'busqueda' if Phase 124 has already flipped it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "041_seed_bot_default_mode"
down_revision = "040_visits"
branch_labels = None
depends_on = None

_KEY = "bot_default_mode"

_UPGRADE_SQL: str = (
    "INSERT INTO bot_settings (key, value, description, updated_at) "
    "VALUES (:key, :value, :description, NOW()) "
    "ON CONFLICT (key) DO NOTHING"
)

_DOWNGRADE_SQL: str = "DELETE FROM bot_settings WHERE key = :key"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(_UPGRADE_SQL),
        {
            "key": _KEY,
            "value": "busqueda",
            "description": "Modo global del bot: recepcionista | busqueda",
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(_DOWNGRADE_SQL), {"key": _KEY})

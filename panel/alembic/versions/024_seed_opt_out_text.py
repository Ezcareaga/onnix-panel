"""Seed wa_tpl_opt_out text into bot_settings.

Revision ID: 024
Revises: 023
Create Date: 2026-04-17

The wa_tpl_opt_out row already exists in bot_settings (seeded in Fase 0)
but its value is empty.  This migration sets the canonical opt-out response
text so that the bot's get_opt_out_text() helper reads from the DB rather
than always falling back to the hardcoded constant.

The upgrade uses INSERT ... ON CONFLICT (key) DO UPDATE so it is safe to run
even if a previous attempt created the row, or if the row already has a
different value set by an operator.

Downgrade reverts the value to '' (the pre-Fase-6 state), matching what
get_opt_out_text() treats as "empty → use fallback".

Reference:
  - Fase 6: business-logic-cleanup / move hardcoded opt-out text to DB
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None

_OPT_OUT_TEXT: str = (
    "Entendido, no te vamos a escribir más.\n"
    "\n"
    "Si en algún momento querés retomar la búsqueda, escribinos cuando quieras."
)

# Exposed as module-level constants so test_migration_024.py can import them
# for drift protection without duplicating SQL strings.
_UPGRADE_SQL: str = (
    "INSERT INTO bot_settings (key, value, updated_at) "
    "VALUES (:key, :value, NOW()) "
    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()"
)

_DOWNGRADE_SQL: str = (
    "UPDATE bot_settings SET value = '', updated_at = NOW() WHERE key = :key"
)

_KEY = "wa_tpl_opt_out"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(_UPGRADE_SQL),
        {"key": _KEY, "value": _OPT_OUT_TEXT},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(_DOWNGRADE_SQL),
        {"key": _KEY},
    )

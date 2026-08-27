"""Seed wa_tpl_ai_dual_fail_text into bot_settings.

Revision ID: 027
Revises: 026
Create Date: 2026-04-17

Inserts the user-facing fallback message displayed when both Claude and Gemini
fail simultaneously. The bot's get_ai_dual_fail_text() helper reads this key
from bot_settings and falls back to the hardcoded DEFAULT_AI_DUAL_FAIL_TEXT
constant when the row is missing or empty.

The upgrade uses INSERT ... ON CONFLICT (key) DO UPDATE so it is safe to run
even if the row already exists or was set by an operator.

Downgrade deletes the row so that get_ai_dual_fail_text() reverts to the
hardcoded constant, matching the pre-Fase-13 state.

Reference:
  - Fase 13: open-points-cleanup / dual-fail returns BotResponse instead of raise
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None

_AI_DUAL_FAIL_TEXT: str = (
    "Perdón, estoy teniendo un problema técnico. Intentá de nuevo en unos minutos. "
    "Si es urgente escribí ASESOR y te contactamos."
)

# Exposed as module-level constants so test_migration_027.py can import them
# for drift protection without duplicating SQL strings.
_UPGRADE_SQL: str = (
    "INSERT INTO bot_settings (key, value, updated_at) "
    "VALUES (:key, :value, NOW()) "
    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()"
)

_DOWNGRADE_SQL: str = (
    "DELETE FROM bot_settings WHERE key = :key"
)

_KEY = "wa_tpl_ai_dual_fail_text"


def upgrade() -> None:
    bind = op.get_bind()
    # Set description separately so the INSERT stays consistent with the
    # migration-024 pattern (key, value, updated_at only).
    bind.execute(
        sa.text(_UPGRADE_SQL),
        {"key": _KEY, "value": _AI_DUAL_FAIL_TEXT},
    )
    bind.execute(
        sa.text(
            "UPDATE bot_settings SET description = :desc WHERE key = :key"
        ),
        {
            "key": _KEY,
            "desc": "Mensaje al cliente cuando Claude y Gemini fallan simultaneamente",
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(_DOWNGRADE_SQL),
        {"key": _KEY},
    )

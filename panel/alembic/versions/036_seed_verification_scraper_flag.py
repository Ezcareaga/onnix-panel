"""Seed scheduler_verification_scraper_enabled into bot_settings.

Revision ID: 036_seed_verif_flag
Revises: 035_on_hold
Create Date: 2026-04-26

Enables the midday verification scraper task (run_verification_scraper) that
re-checks portal URLs for active properties and sets on_hold=true for listings
paused by the agent.  The scheduler reads this flag at startup via SettingsManager.

Uses INSERT ... ON CONFLICT (key) DO NOTHING so re-running is safe and does
NOT overwrite a value an operator may have changed from the panel.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "036_seed_verif_flag"
down_revision = "035_on_hold"
branch_labels = None
depends_on = None

_KEY = "scheduler_verification_scraper_enabled"

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
            "value": "true",
            "description": (
                "Enables midday verification scraper that re-checks portal URLs "
                "for active properties"
            ),
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(_DOWNGRADE_SQL), {"key": _KEY})

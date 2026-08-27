"""Seed properties_chatbot_enabled into bot_settings.

Revision ID: 038_seed_chatbot_flag
Revises: 037_portal_dates
Create Date: 2026-04-26

Uses INSERT ... ON CONFLICT (key) DO NOTHING so re-running is safe and does
NOT overwrite a value an operator may have changed from the panel.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "038_seed_chatbot_flag"
down_revision = "037_portal_dates"
branch_labels = None
depends_on = None

_KEY = "properties_chatbot_enabled"

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
                "Enables the natural-language search chatbot in the properties panel"
            ),
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(_DOWNGRADE_SQL), {"key": _KEY})

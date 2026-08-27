"""Seed followup toggle defaults into bot_settings.

Revision ID: 023
Revises: 022
Create Date: 2026-04-17

The followup_sender scheduler task (app/bot/scheduler/tasks/followup_sender.py)
reads three boolean toggle keys from bot_settings and falls back to hard-coded
defaults when the rows are absent (fail-open).  Without the rows, la administradora
cannot see or modify these toggles through the panel's generic settings table
(templates/partials/settings_form.html).

This migration seeds the three rows so they become visible and editable in the
panel UI.  Re-running the migration is safe: ON CONFLICT (key) DO NOTHING
preserves any value Ez has already set.

Reference:
  - docs/AUDIT_TEMPLATES_RECURRENTES_20260415.md
  - docs/PLAN_CONV_REFACTOR_20260415.md
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None

_SEEDED_ROWS: list[tuple[str, str, str]] = [
    (
        "followup_24h_enabled",
        "true",
        "Habilitar envío de seguimiento a 24h post-silencio",
    ),
    (
        "followup_72h_enabled",
        "true",
        "Habilitar envío de seguimiento a 72h post-silencio",
    ),
    (
        "followup_96h_discard",
        "false",
        "Habilitar auto-descarte a 96h tras seguimientos agotados",
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    for key, value, description in _SEEDED_ROWS:
        bind.execute(
            sa.text(
                "INSERT INTO bot_settings (key, value, description, updated_at) "
                "VALUES (:key, :value, :description, NOW()) "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {"key": key, "value": value, "description": description},
        )


def downgrade() -> None:
    bind = op.get_bind()
    for key, _, _ in _SEEDED_ROWS:
        bind.execute(
            sa.text("DELETE FROM bot_settings WHERE key = :key"),
            {"key": key},
        )

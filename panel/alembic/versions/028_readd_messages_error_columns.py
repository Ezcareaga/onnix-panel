"""Re-add messages.error_code and messages.error_message.

Revision ID: 028
Revises: 027
Create Date: 2026-04-17

Migration 026 (drop_dead_columns) dropped these two columns from the messages
table because they had zero live reads at the time.  Fase 14 introduces the
WA permanent-failure marker which writes status='failed', error_code, and
error_message when Twilio retries are exhausted.  Re-adding them is
necessary before the marker code can execute.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS error_code VARCHAR(20)"
    ))
    op.execute(sa.text(
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS error_message TEXT"
    ))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE messages DROP COLUMN IF EXISTS error_code"))
    op.execute(sa.text("ALTER TABLE messages DROP COLUMN IF EXISTS error_message"))

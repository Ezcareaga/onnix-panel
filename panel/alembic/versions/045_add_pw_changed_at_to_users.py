"""Add pw_changed_at column to users for session invalidation on password change.

Revision ID: 045_add_pw_changed_at_to_users
Revises: 044_contact_reminders
Create Date: 2026-06-13

Column:
  pw_changed_at  TIMESTAMPTZ NULL  (NULL = never changed / legacy row; no backfill needed)

Used by session hardening (D2): sessions issued before pw_changed_at are invalidated
so that changing a password kicks out all other active sessions immediately.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "045_add_pw_changed_at_to_users"
down_revision = "044_contact_reminders"
branch_labels = None
depends_on = None

TABLE = "users"
COLUMN = "pw_changed_at"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)

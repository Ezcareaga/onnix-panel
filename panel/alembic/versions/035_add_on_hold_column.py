"""Add on_hold column to properties.

Revision ID: 035_on_hold
Revises: 034_m5_accents
Create Date: 2026-04-26

Tracks Remax listings flagged OnHoldListing=true by the portal (paused by the
agent — not deleted, not searchable). The midday verification scraper populates
this column.  Default false keeps all existing rows unchanged.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "035_on_hold"
down_revision = "034_m5_accents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "properties",
        sa.Column("on_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_properties_on_hold",
        "properties",
        ["on_hold"],
        postgresql_where=sa.text("on_hold = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_properties_on_hold", table_name="properties")
    op.drop_column("properties", "on_hold")

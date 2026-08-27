"""Add portal_listed_at and portal_expires_at columns to properties.

Revision ID: 037_portal_dates
Revises: 036_seed_verif_flag
Create Date: 2026-04-26

Tracks when a listing was first published on the source portal
(portal_listed_at) and when the exclusivity contract expires per portal
(portal_expires_at). Both nullable — existing rows are unaffected.

Partial index on updated_at WHERE is_active = false speeds up inactive-property
queries (e.g. nightly reactivation checks, admin listing cleanup).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "037_portal_dates"
down_revision = "036_seed_verif_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "properties",
        sa.Column("portal_listed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "properties",
        sa.Column("portal_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_properties_inactive_updated",
        "properties",
        ["updated_at"],
        postgresql_ops={"updated_at": "DESC"},
        postgresql_where=sa.text("is_active = false"),
    )


def downgrade() -> None:
    op.drop_index("ix_properties_inactive_updated", table_name="properties")
    op.drop_column("properties", "portal_expires_at")
    op.drop_column("properties", "portal_listed_at")

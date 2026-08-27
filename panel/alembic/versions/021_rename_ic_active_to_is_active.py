"""Rename infocasas_properties.active → is_active for naming consistency.

Revision ID: 021
Revises: 020
Create Date: 2026-04-16

Renames the `active` column in `infocasas_properties` to `is_active` so that
it matches the naming convention used by the `properties` table and the rest
of the ORM layer.

This is a pure column rename — no data is altered.
"""
from __future__ import annotations

from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "infocasas_properties",
        "active",
        new_column_name="is_active",
    )


def downgrade() -> None:
    op.alter_column(
        "infocasas_properties",
        "is_active",
        new_column_name="active",
    )

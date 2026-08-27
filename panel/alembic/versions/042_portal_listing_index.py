"""Partial index for public portal listing (M6.4b).

Revision ID: 042_portal_listing_index
Revises: 041_seed_bot_default_mode
Create Date: 2026-06-10

Covers the GET /propiedades listing query emitted by _build_filter_sql
(panel/app/repositories/property_repo.py):

    WHERE source = 'onnixpy' AND is_active = TRUE AND on_hold = FALSE
    ORDER BY created_at DESC LIMIT 24 OFFSET n

and the matching COUNT(*). Without it the query scans ~9K rows (23-68ms);
with the partial index it drops to ~1-2ms.

The index predicate must be IMPLIED by the real WHERE clause, hence
`on_hold = FALSE` (not `IS NOT TRUE`) and no `duplicate_of` in the
predicate — the planner can still use the index when the query adds
`duplicate_of IS NULL` as a filter.
"""
from __future__ import annotations

from alembic import op

revision = "042_portal_listing_index"
down_revision = "041_seed_bot_default_mode"
branch_labels = None
depends_on = None

INDEX_NAME = "idx_properties_portal_created"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} "
        "ON properties (created_at DESC) "
        "WHERE source = 'onnixpy' AND is_active = TRUE AND on_hold = FALSE"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")

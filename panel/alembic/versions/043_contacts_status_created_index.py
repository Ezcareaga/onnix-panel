"""Index (status, created_at DESC) on contacts for list queries.

Revision ID: 043_contacts_status_idx
Revises: 042_portal_listing_index
Create Date: 2026-06-12

Covers the GET /contacts listing query (contact_repo.get_all / count_all):

    WHERE status = :status
    ORDER BY created_at DESC

The composite index on (status, created_at DESC) lets the planner satisfy
both the equality filter and the sort in a single index scan, eliminating
the sequential scan + sort on the ~10K contacts table.

DESC on created_at matches the ORDER BY direction so no extra sort step
is needed; bitmapped index scans still work for the unfiltered case.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "043_contacts_status_idx"  # <=32 chars: alembic_version is varchar(32)
down_revision = "042_portal_listing_index"
branch_labels = None
depends_on = None

INDEX_NAME = "idx_contacts_status_created"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "contacts",
        [
            "status",
            sa.text("created_at DESC"),
        ],
        postgresql_ops={"created_at": "DESC"},
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="contacts")

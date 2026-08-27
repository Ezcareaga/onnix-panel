"""Add image download tracking columns to properties

Revision ID: 010
Revises: 009
Create Date: 2026-03-12

Changes:
- properties.images_downloaded: BOOLEAN DEFAULT false
- properties.images_downloaded_at: TIMESTAMP (nullable)
- properties.local_image_count: INTEGER DEFAULT 0
"""

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.execute(
        "ALTER TABLE properties ADD COLUMN IF NOT EXISTS images_downloaded BOOLEAN DEFAULT false"
    )
    op.execute(
        "ALTER TABLE properties ADD COLUMN IF NOT EXISTS images_downloaded_at TIMESTAMP"
    )
    op.execute(
        "ALTER TABLE properties ADD COLUMN IF NOT EXISTS local_image_count INTEGER DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE properties DROP COLUMN IF EXISTS images_downloaded")
    op.execute("ALTER TABLE properties DROP COLUMN IF EXISTS images_downloaded_at")
    op.execute("ALTER TABLE properties DROP COLUMN IF EXISTS local_image_count")

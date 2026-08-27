"""Widen area columns from numeric(10,2) to numeric(15,2)

Revision ID: 009
Revises: 008
Create Date: 2026-03-11

Fixes: Large estancias in the Chaco (>10,000 ha = >100M m²) cause
"numeric field overflow" on INSERT because NUMERIC(10,2) max is 99,999,999.99.

Changes:
- properties.total_area_m2: numeric(10,2) → numeric(15,2)
- properties.built_area_m2: numeric(10,2) → numeric(15,2)
- infocasas_properties.total_area_m2: numeric(10,2) → numeric(15,2)
- infocasas_properties.built_area_m2: numeric(10,2) → numeric(15,2)
"""
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE properties ALTER COLUMN total_area_m2 TYPE numeric(15,2)")
    op.execute("ALTER TABLE properties ALTER COLUMN built_area_m2 TYPE numeric(15,2)")
    op.execute("ALTER TABLE infocasas_properties ALTER COLUMN total_area_m2 TYPE numeric(15,2)")
    op.execute("ALTER TABLE infocasas_properties ALTER COLUMN built_area_m2 TYPE numeric(15,2)")


def downgrade() -> None:
    op.execute("ALTER TABLE properties ALTER COLUMN total_area_m2 TYPE numeric(10,2)")
    op.execute("ALTER TABLE properties ALTER COLUMN built_area_m2 TYPE numeric(10,2)")
    op.execute("ALTER TABLE infocasas_properties ALTER COLUMN total_area_m2 TYPE numeric(10,2)")
    op.execute("ALTER TABLE infocasas_properties ALTER COLUMN built_area_m2 TYPE numeric(10,2)")

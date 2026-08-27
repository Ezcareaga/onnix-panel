"""Add infocasas_ref column to contacts for IC property cross-reference

Revision ID: 008
Revises: 007
Create Date: 2026-03-06

Changes:
- New column: contacts.infocasas_ref (VARCHAR(20))
- Index for fast lookups
- Stores the InfoCasas property reference code (e.g. VD6313) for IC leads
"""

revision = '008'
down_revision = '007'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column('contacts', sa.Column('infocasas_ref', sa.String(20), nullable=True))
    op.create_index('idx_contacts_infocasas_ref', 'contacts', ['infocasas_ref'])


def downgrade():
    op.drop_index('idx_contacts_infocasas_ref', 'contacts')
    op.drop_column('contacts', 'infocasas_ref')

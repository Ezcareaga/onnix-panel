"""Create infocasas_properties table for cross-referencing

Revision ID: 007
Revises: 006
Create Date: 2026-03-06

Changes:
- New table: infocasas_properties
- Stores scraped InfoCasas property data
- FK to properties table for matching
- GIN index on title for trigram similarity matching
"""

revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        'infocasas_properties',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('infocasas_id', sa.String(20), nullable=False),
        sa.Column('infocasas_ref', sa.String(20), nullable=False),
        sa.Column('title', sa.Text),
        sa.Column('price_sale', sa.Numeric(15, 2)),
        sa.Column('price_rent', sa.Numeric(15, 2)),
        sa.Column('currency_sale', sa.String(5)),
        sa.Column('currency_rent', sa.String(5)),
        sa.Column('property_type', sa.String(50)),
        sa.Column('operation', sa.String(20)),
        sa.Column('city', sa.String(100)),
        sa.Column('neighborhood', sa.String(100)),
        sa.Column('department', sa.String(100)),
        sa.Column('address', sa.Text),
        sa.Column('lat', sa.Numeric(12, 8)),
        sa.Column('lng', sa.Numeric(12, 8)),
        sa.Column('url', sa.Text),
        sa.Column('bedrooms', sa.SmallInteger),
        sa.Column('bathrooms', sa.SmallInteger),
        sa.Column('total_area_m2', sa.Numeric(10, 2)),
        sa.Column('built_area_m2', sa.Numeric(10, 2)),
        sa.Column('consultas', sa.Integer, server_default='0'),
        sa.Column('visitas', sa.Integer, server_default='0'),
        sa.Column('whatsapp', sa.Integer, server_default='0'),
        sa.Column('vendedor', sa.String(200)),
        sa.Column('active', sa.Boolean, server_default='true'),
        sa.Column('property_id', sa.Integer, sa.ForeignKey('properties.id'), nullable=True),
        sa.Column('matched_by', sa.String(50)),
        sa.Column('raw_data', sa.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime, server_default=sa.text('NOW()')),
        sa.UniqueConstraint('infocasas_id', name='uq_infocasas_id'),
        sa.UniqueConstraint('infocasas_ref', name='uq_infocasas_ref'),
    )

    op.create_index('idx_infocasas_ref', 'infocasas_properties', ['infocasas_ref'])
    op.create_index('idx_infocasas_property_id', 'infocasas_properties', ['property_id'])
    op.execute("CREATE INDEX idx_infocasas_title_trgm ON infocasas_properties USING GIN (title gin_trgm_ops)")


def downgrade():
    op.drop_index('idx_infocasas_title_trgm', 'infocasas_properties')
    op.drop_index('idx_infocasas_property_id', 'infocasas_properties')
    op.drop_index('idx_infocasas_ref', 'infocasas_properties')
    op.drop_table('infocasas_properties')

"""Add 'deleted' to contacts.status CHECK constraint for soft-delete

Revision ID: 012
Revises: 011
Create Date: 2026-03-21

Changes:
- Drop existing contacts_status_check constraint
- Re-create it including 'deleted' as valid value
- Soft-deleted contacts stay in DB but are excluded from UI listings
"""

revision = '012'
down_revision = '011'
branch_labels = None
depends_on = None

from alembic import op


def upgrade():
    op.drop_constraint('contacts_status_check', 'contacts', type_='check')
    op.create_check_constraint(
        'contacts_status_check',
        'contacts',
        "status IN ('new','contacted','hot','interview','cold','baja','deleted')",
    )


def downgrade():
    op.drop_constraint('contacts_status_check', 'contacts', type_='check')
    op.create_check_constraint(
        'contacts_status_check',
        'contacts',
        "status IN ('new','contacted','hot','interview','cold','baja')",
    )

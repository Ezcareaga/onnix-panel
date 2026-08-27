"""Add telegram to conversations channel check constraint

Revision ID: 003
Revises: 002
Create Date: 2026-02-24
"""
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_channel_check")
    op.execute("""
        ALTER TABLE conversations ADD CONSTRAINT conversations_channel_check
        CHECK (channel IN ('whatsapp', 'web', 'manual', 'telegram'))
    """)

def downgrade() -> None:
    op.execute("ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_channel_check")
    op.execute("""
        ALTER TABLE conversations ADD CONSTRAINT conversations_channel_check
        CHECK (channel IN ('whatsapp', 'web', 'manual'))
    """)

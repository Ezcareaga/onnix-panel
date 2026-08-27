"""Add infocasas_inquiry_history table and remove inert enforce_baja_terminal trigger.

Revision ID: 022
Revises: 021
Create Date: 2026-04-16

The trigger enforce_baja_terminal checks OLD.status = 'baja', but the system
renamed baja -> discarded in migrations 012-018. No contact has status 'baja',
so the trigger is dead code.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create inquiry history table
    op.create_table(
        "infocasas_inquiry_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("contact_id", sa.Integer, sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("infocasas_ref", sa.String(20), nullable=False),
        sa.Column("consulta_id", sa.String(100), nullable=True),
        sa.Column("consulta_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("property_title", sa.String(200), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "idx_inquiry_history_contact",
        "infocasas_inquiry_history",
        ["contact_id"],
    )

    # 2. Remove inert trigger + function (checks 'baja' which no longer exists)
    op.execute("DROP TRIGGER IF EXISTS enforce_baja_terminal ON contacts")
    op.execute("DROP FUNCTION IF EXISTS prevent_baja_reversal()")


def downgrade() -> None:
    # Restore trigger + function
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_baja_reversal()
        RETURNS TRIGGER AS $$
        BEGIN
          IF OLD.status = 'baja' AND NEW.status != 'baja' THEN
            RAISE EXCEPTION 'Cannot reverse baja status for contact %', OLD.id;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER enforce_baja_terminal
        BEFORE UPDATE ON contacts
        FOR EACH ROW
        EXECUTE FUNCTION prevent_baja_reversal()
    """)

    # Drop inquiry history
    op.drop_index("idx_inquiry_history_contact", table_name="infocasas_inquiry_history")
    op.drop_table("infocasas_inquiry_history")

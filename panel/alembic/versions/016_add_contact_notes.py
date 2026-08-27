"""Add contact_notes table.

Revision ID: 016
Revises: 015
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contact_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_contact_notes_contact_id", "contact_notes", ["contact_id"])


def downgrade() -> None:
    op.drop_index("idx_contact_notes_contact_id")
    op.drop_table("contact_notes")

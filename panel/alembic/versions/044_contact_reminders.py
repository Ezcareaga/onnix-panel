"""Add contact_reminders table for follow-up scheduling.

Revision ID: 044_contact_reminders
Revises: 043_contacts_status_created_index
Create Date: 2026-06-12

Columns:
  id          PK SERIAL
  contact_id  FK contacts(id) ON DELETE CASCADE NOT NULL
  user_id     FK users(id) NOT NULL  (who created the reminder)
  due_at      timestamptz NOT NULL   (when to act)
  note        varchar(500) NOT NULL
  done_at     timestamptz NULL        (NULL = open; set when marked done)
  created_at  timestamptz NOT NULL default now()

Index on (due_at) WHERE done_at IS NULL supports the list_due() query.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "044_contact_reminders"
down_revision = "043_contacts_status_idx"
branch_labels = None
depends_on = None

TABLE = "contact_reminders"
INDEX_DUE = "idx_contact_reminders_due_open"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "contact_id",
            sa.Integer(),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        INDEX_DUE,
        TABLE,
        ["due_at"],
        postgresql_where=sa.text("done_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_DUE, table_name=TABLE)
    op.drop_table(TABLE)

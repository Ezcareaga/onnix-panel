"""Drop dead DB columns confirmed to have zero live reads in panel/app/.

Revision ID: 026
Revises: 025
Create Date: 2026-04-17

Pre-verified zero live reads in panel/app/; replaced by newer fields.
Reference: Fase 8 business-logic-cleanup plan.

Columns dropped (12 total):

contacts (9):
  - interest_operation  — written once at create, never read
  - interest_type       — written once at create, never read
  - interest_city       — written once at create, never read
  - interest_min_price  — written once at create, never read
  - interest_max_price  — written once at create, never read
  - interest_bedrooms   — written once at create, never read
  - original_data       — JSONB, never referenced
  - last_contact_at     — duplicate of last_activity_at, zero reads
  - assigned_to         — superseded by agent_user_id, zero live refs

messages (2):
  - error_code          — zero writes, zero reads
  - error_message       — written in logging path only, zero reads

lead_events (1):
  - assigned_to         — superseded by event_metadata JSONB, zero refs

NOTE: conversations.updated_at is intentionally NOT dropped — it is actively
read at bot/webhooks/telegram.py:155 and bot/webhooks/whatsapp.py:202 via
"ORDER BY c.updated_at DESC LIMIT 1". The pre-audit claim of zero reads was
incorrect; this column is live and excluded from this migration.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None

# Drift guard — imported by test_migration_026.py to avoid duplication.
# Each tuple: (table_name, column_name)
_DROPPED_COLUMNS: list[tuple[str, str]] = [
    ("contacts", "interest_operation"),
    ("contacts", "interest_type"),
    ("contacts", "interest_city"),
    ("contacts", "interest_min_price"),
    ("contacts", "interest_max_price"),
    ("contacts", "interest_bedrooms"),
    ("contacts", "original_data"),
    ("contacts", "last_contact_at"),
    ("contacts", "assigned_to"),
    ("messages", "error_code"),
    ("messages", "error_message"),
    ("lead_events", "assigned_to"),
]


def upgrade() -> None:
    # Step 1: Drop FK constraints before dropping columns.
    # Use IF EXISTS so the migration is safe to re-run on a DB where constraints
    # were already dropped manually.
    op.execute(sa.text(
        "ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_assigned_to_fkey"
    ))
    op.execute(sa.text(
        "ALTER TABLE lead_events DROP CONSTRAINT IF EXISTS lead_events_assigned_to_fkey"
    ))

    # Step 2: Drop the columns. Use IF EXISTS for idempotency.
    op.execute(sa.text("ALTER TABLE contacts DROP COLUMN IF EXISTS interest_operation"))
    op.execute(sa.text("ALTER TABLE contacts DROP COLUMN IF EXISTS interest_type"))
    op.execute(sa.text("ALTER TABLE contacts DROP COLUMN IF EXISTS interest_city"))
    op.execute(sa.text("ALTER TABLE contacts DROP COLUMN IF EXISTS interest_min_price"))
    op.execute(sa.text("ALTER TABLE contacts DROP COLUMN IF EXISTS interest_max_price"))
    op.execute(sa.text("ALTER TABLE contacts DROP COLUMN IF EXISTS interest_bedrooms"))
    op.execute(sa.text("ALTER TABLE contacts DROP COLUMN IF EXISTS original_data"))
    op.execute(sa.text("ALTER TABLE contacts DROP COLUMN IF EXISTS last_contact_at"))
    op.execute(sa.text("ALTER TABLE contacts DROP COLUMN IF EXISTS assigned_to"))
    op.execute(sa.text("ALTER TABLE messages DROP COLUMN IF EXISTS error_code"))
    op.execute(sa.text("ALTER TABLE messages DROP COLUMN IF EXISTS error_message"))
    op.execute(sa.text("ALTER TABLE lead_events DROP COLUMN IF EXISTS assigned_to"))


def downgrade() -> None:
    # Restore columns — empty, no data recovery.
    op.add_column(
        "contacts",
        sa.Column("interest_operation", sa.String(20), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("interest_type", sa.String(50), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("interest_city", sa.String(100), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("interest_min_price", sa.Numeric(15, 2), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("interest_max_price", sa.Numeric(15, 2), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("interest_bedrooms", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("original_data", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("last_contact_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("assigned_to", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "contacts_assigned_to_fkey",
        "contacts",
        "users",
        ["assigned_to"],
        ["id"],
    )

    op.add_column(
        "messages",
        sa.Column("error_code", sa.String(20), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("error_message", sa.Text(), nullable=True),
    )

    op.add_column(
        "lead_events",
        sa.Column("assigned_to", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "lead_events_assigned_to_fkey",
        "lead_events",
        "users",
        ["assigned_to"],
        ["id"],
    )

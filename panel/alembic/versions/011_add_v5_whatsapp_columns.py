"""Add v5 WhatsApp columns and seed bot_settings

Revision ID: 011
Revises: 010
Create Date: 2026-03-20

Changes:
- conversations.last_human_reply_at: TIMESTAMPTZ (nullable)
- contacts.last_user_message_at: TIMESTAMPTZ (nullable) + index
- Backfill last_user_message_at from existing inbound messages
- Seed bot_settings: whatsapp_mode, human_cooldown_minutes
"""

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    # 1. conversations: add last_human_reply_at
    op.execute(
        "ALTER TABLE conversations "
        "ADD COLUMN IF NOT EXISTS last_human_reply_at TIMESTAMPTZ"
    )

    # 2. contacts: add last_user_message_at
    op.execute(
        "ALTER TABLE contacts "
        "ADD COLUMN IF NOT EXISTS last_user_message_at TIMESTAMPTZ"
    )

    # 3. Index for 24h window queries
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_contacts_last_user_msg "
        "ON contacts(last_user_message_at DESC NULLS LAST)"
    )

    # 4. Backfill last_user_message_at from existing inbound messages
    op.execute(
        "UPDATE contacts c "
        "SET last_user_message_at = sub.last_inbound "
        "FROM ("
        "  SELECT contact_id, MAX(created_at) AS last_inbound "
        "  FROM messages "
        "  WHERE direction = 'inbound' AND contact_id IS NOT NULL "
        "  GROUP BY contact_id"
        ") sub "
        "WHERE c.id = sub.contact_id "
        "  AND c.last_user_message_at IS NULL"
    )

    # 5. Seed bot_settings for v5 WhatsApp
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO bot_settings (key, value, description) "
            "VALUES (:key, :value, :desc) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {"key": "whatsapp_mode", "value": "manual",
         "desc": "WhatsApp bot mode: manual (human only) or auto (bot responds)"}
    )
    bind.execute(
        sa.text(
            "INSERT INTO bot_settings (key, value, description) "
            "VALUES (:key, :value, :desc) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {"key": "human_cooldown_minutes", "value": "30",
         "desc": "Minutes bot stays silent after human agent replies"}
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "DELETE FROM bot_settings WHERE key IN ('whatsapp_mode', 'human_cooldown_minutes')"
    ))
    op.execute("DROP INDEX IF EXISTS idx_contacts_last_user_msg")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS last_user_message_at")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS last_human_reply_at")

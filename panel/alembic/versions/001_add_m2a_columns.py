"""Add M2A columns and tables

Revision ID: 001
Revises:
Create Date: 2026-02-23

Requirements covered: MIGA-01 (partial), MIGA-02, MIGA-03, MIGA-04, MIGA-05, MIGA-06, MIGA-07 (partial)
"""
from alembic import op

# revision identifiers
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ================================================================
    # 1. CREATE TABLE lead_events (MIGA-06)
    # ================================================================
    op.execute("""
        CREATE TABLE lead_events (
            id              SERIAL PRIMARY KEY,
            contact_id      INTEGER NOT NULL REFERENCES contacts(id),
            event_type      VARCHAR(50) NOT NULL,
            old_status      VARCHAR(20),
            new_status      VARCHAR(20),
            triggered_by    VARCHAR(50) NOT NULL DEFAULT 'system',
            assigned_to     INTEGER REFERENCES users(id),
            metadata        JSONB DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_lead_events_contact ON lead_events(contact_id, created_at DESC)")
    op.execute("CREATE INDEX idx_lead_events_type ON lead_events(event_type)")
    op.execute("CREATE INDEX idx_lead_events_created ON lead_events(created_at DESC)")

    # ================================================================
    # 2. CREATE TABLE bot_settings (MIGA-07 — structure only, data in 002)
    # ================================================================
    op.execute("""
        CREATE TABLE bot_settings (
            key             VARCHAR(50) PRIMARY KEY,
            value           TEXT NOT NULL,
            description     TEXT,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by      INTEGER REFERENCES users(id)
        )
    """)

    # ================================================================
    # 3. ALTER TABLE contacts (MIGA-02)
    #    Adding 5 new columns + 1 unique constraint + 3 indexes
    #    PRESERVES all 10,812 existing rows (all new columns are nullable)
    # ================================================================
    op.execute("ALTER TABLE contacts ADD COLUMN phone_normalized VARCHAR(20)")
    op.execute("ALTER TABLE contacts ADD COLUMN source_id VARCHAR(100)")
    op.execute("ALTER TABLE contacts ADD COLUMN property_id INTEGER REFERENCES properties(id)")
    op.execute("ALTER TABLE contacts ADD COLUMN first_message TEXT")
    op.execute("ALTER TABLE contacts ADD COLUMN last_activity_at TIMESTAMPTZ")
    # UNIQUE constraint: NULLs are distinct in PostgreSQL, so 10,812 rows with
    # source_id=NULL won't conflict. Safe to apply on existing data.
    op.execute("ALTER TABLE contacts ADD CONSTRAINT uq_contacts_source UNIQUE (source, source_id)")
    op.execute("CREATE INDEX idx_contacts_source ON contacts(source)")
    op.execute("CREATE INDEX idx_contacts_last_activity ON contacts(last_activity_at DESC NULLS LAST)")
    op.execute("CREATE INDEX idx_contacts_created ON contacts(created_at DESC)")

    # ================================================================
    # 4. ALTER TABLE conversations (MIGA-03)
    #    Table has 0 rows — safe for any changes
    # ================================================================
    op.execute("ALTER TABLE conversations ADD COLUMN platform VARCHAR(20)")
    op.execute("ALTER TABLE conversations ADD COLUMN platform_chat_id VARCHAR(100)")
    op.execute("ALTER TABLE conversations ADD COLUMN is_bot_active BOOLEAN DEFAULT TRUE")
    op.execute("ALTER TABLE conversations ADD COLUMN is_open BOOLEAN DEFAULT TRUE")
    op.execute("ALTER TABLE conversations ADD COLUMN message_count INTEGER DEFAULT 0")
    op.execute("""
        ALTER TABLE conversations ADD CONSTRAINT uq_conversation_platform
        UNIQUE (contact_id, platform, platform_chat_id)
    """)
    op.execute("CREATE INDEX idx_conversations_open ON conversations(is_open, last_message_at DESC NULLS LAST)")

    # ================================================================
    # 5. ALTER TABLE messages (MIGA-04)
    #    Table has 0 rows — safe for any changes
    # ================================================================
    op.execute("ALTER TABLE messages ADD COLUMN contact_id INTEGER REFERENCES contacts(id)")
    op.execute("ALTER TABLE messages ADD COLUMN content TEXT")
    op.execute("ALTER TABLE messages ADD COLUMN intent VARCHAR(30)")
    op.execute("ALTER TABLE messages ADD COLUMN properties_shown INTEGER[]")
    op.execute("ALTER TABLE messages ADD COLUMN external_id VARCHAR(100)")
    op.execute("ALTER TABLE messages ADD COLUMN status VARCHAR(20) DEFAULT 'sent'")
    op.execute("ALTER TABLE messages ADD COLUMN error_code VARCHAR(20)")
    op.execute("ALTER TABLE messages ADD COLUMN error_message TEXT")
    op.execute("ALTER TABLE messages ADD COLUMN ai_model VARCHAR(50)")
    op.execute("ALTER TABLE messages ADD COLUMN ai_tokens_in INTEGER")
    op.execute("ALTER TABLE messages ADD COLUMN ai_tokens_out INTEGER")
    op.execute("ALTER TABLE messages ADD COLUMN ai_latency_ms INTEGER")
    # UNIQUE on external_id for idempotency
    op.execute("ALTER TABLE messages ADD CONSTRAINT uq_messages_external UNIQUE (external_id)")
    # Update direction CHECK to include 'system' (table is empty, safe)
    op.execute("ALTER TABLE messages DROP CONSTRAINT messages_direction_check")
    op.execute("ALTER TABLE messages ADD CONSTRAINT messages_direction_check CHECK (direction IN ('inbound', 'outbound', 'system'))")
    # Index for querying messages by contact
    op.execute("CREATE INDEX idx_messages_contact ON messages(contact_id, created_at DESC)")

    # ================================================================
    # 6. ALTER TABLE users (MIGA-05)
    #    Table has 1 row — admin. username added as nullable, updated, then UNIQUE applied.
    # ================================================================
    op.execute("ALTER TABLE users ADD COLUMN username VARCHAR(50)")
    op.execute("ALTER TABLE users ADD COLUMN display_name VARCHAR(200)")
    op.execute("ALTER TABLE users ADD COLUMN phone VARCHAR(20)")
    # Set existing admin's username so we can apply UNIQUE
    op.execute("UPDATE users SET username = 'admin', display_name = name WHERE id = 1")
    # Now add UNIQUE constraint on username
    op.execute("ALTER TABLE users ADD CONSTRAINT uq_users_username UNIQUE (username)")


def downgrade() -> None:
    # ================================================================
    # Reverse all changes in reverse order
    # ================================================================

    # 6. users — remove new columns
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS uq_users_username")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS phone")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS display_name")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS username")

    # 5. messages — remove new columns, restore CHECK
    op.execute("DROP INDEX IF EXISTS idx_messages_contact")
    op.execute("ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_direction_check")
    op.execute("ALTER TABLE messages ADD CONSTRAINT messages_direction_check CHECK (direction IN ('inbound', 'outbound'))")
    op.execute("ALTER TABLE messages DROP CONSTRAINT IF EXISTS uq_messages_external")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS ai_latency_ms")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS ai_tokens_out")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS ai_tokens_in")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS ai_model")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS error_message")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS error_code")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS status")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS external_id")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS properties_shown")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS intent")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS content")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS contact_id")

    # 4. conversations — remove new columns
    op.execute("DROP INDEX IF EXISTS idx_conversations_open")
    op.execute("ALTER TABLE conversations DROP CONSTRAINT IF EXISTS uq_conversation_platform")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS message_count")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS is_open")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS is_bot_active")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS platform_chat_id")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS platform")

    # 3. contacts — remove new columns
    op.execute("DROP INDEX IF EXISTS idx_contacts_created")
    op.execute("DROP INDEX IF EXISTS idx_contacts_last_activity")
    op.execute("DROP INDEX IF EXISTS idx_contacts_source")
    op.execute("ALTER TABLE contacts DROP CONSTRAINT IF EXISTS uq_contacts_source")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS last_activity_at")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS first_message")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS property_id")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS source_id")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS phone_normalized")

    # 2. bot_settings
    op.execute("DROP TABLE IF EXISTS bot_settings")

    # 1. lead_events
    op.execute("DROP INDEX IF EXISTS idx_lead_events_created")
    op.execute("DROP INDEX IF EXISTS idx_lead_events_type")
    op.execute("DROP INDEX IF EXISTS idx_lead_events_contact")
    op.execute("DROP TABLE IF EXISTS lead_events")

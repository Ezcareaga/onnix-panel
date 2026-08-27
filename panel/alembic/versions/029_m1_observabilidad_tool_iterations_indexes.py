"""M1 Observabilidad: add messages.tool_iterations + observability indexes.

Revision ID: 029_m1_observabilidad
Revises: 028
Create Date: 2026-04-18

Adds a SMALLINT nullable column to record how many Claude tool-use iterations
were performed per bot message, plus three indexes that speed up dashboard
queries used in the M1 observability panel.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "029_m1_observabilidad"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("tool_iterations", sa.SmallInteger(), nullable=True),
    )
    op.create_index(
        "idx_messages_created_at",
        "messages",
        ["created_at"],
    )
    op.create_index(
        "idx_bot_errors_created_at",
        "bot_errors",
        ["created_at"],
    )
    op.create_index(
        "idx_messages_bot_ai_model_created",
        "messages",
        ["ai_model", "created_at"],
        postgresql_where=sa.text("sender_type = 'bot'"),
    )


def downgrade() -> None:
    op.drop_index("idx_messages_bot_ai_model_created", table_name="messages")
    op.drop_index("idx_bot_errors_created_at", table_name="bot_errors")
    op.drop_index("idx_messages_created_at", table_name="messages")
    op.drop_column("messages", "tool_iterations")

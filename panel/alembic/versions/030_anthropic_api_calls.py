"""M1 Observabilidad Fase J: add anthropic_api_calls table for per-source attribution.

Revision ID: 030_anthropic_api_calls
Revises: 029_m1_observabilidad
Create Date: 2026-04-18

Tracks every Anthropic API call with source attribution (bot.orchestrator,
property_classifier, bot.lead_profiler, etc.) plus token counts and cost.
Enables the per-source time-series dashboard introduced in Fase J.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "030_anthropic_api_calls"
down_revision = "029_m1_observabilidad"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "anthropic_api_calls",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_creation_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_anthropic_calls_source_created",
        "anthropic_api_calls",
        ["source", "created_at"],
    )
    op.create_index(
        "idx_anthropic_calls_created",
        "anthropic_api_calls",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_anthropic_calls_source_created", table_name="anthropic_api_calls")
    op.drop_index("idx_anthropic_calls_created", table_name="anthropic_api_calls")
    op.drop_table("anthropic_api_calls")

"""GSD v17 — estados split: contacted→bot_replied, eliminar visit_scheduled/negotiation.

Revision ID: 018
Revises: 017
Create Date: 2026-04-05

Changes:
- Pre-condition: abort if any contacts have visit_scheduled or negotiation status
- Expand CHECK constraint to allow both old and new states (transition window)
- Migrate all 'contacted' contacts → 'bot_replied'
- Write traceability lead_events for each migrated contact
- Insert ic_autoreply_reenviados_enabled=false into bot_settings
- Contract CHECK constraint to final states only (removes contacted/visit_scheduled/negotiation)
- Add agent_user_id INTEGER REFERENCES users(id) to contacts
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None

# Estados válidos después de esta migration (estado final)
_FINAL_STATUSES = (
    "new", "bot_replied", "agent_replied", "interested",
    "closed", "no_response", "discarded", "deleted",
)

# Estados durante la transición (old + new, para no romper nada mientras migra datos)
_TRANSITION_STATUSES = (
    "new", "contacted", "bot_replied", "agent_replied", "interested",
    "visit_scheduled", "negotiation", "closed", "no_response", "discarded", "deleted",
)

# Estados originales (para downgrade)
_ORIGINAL_STATUSES = (
    "new", "contacted", "interested", "visit_scheduled", "negotiation",
    "closed", "no_response", "discarded", "deleted",
)


def _make_check(statuses: tuple[str, ...]) -> str:
    """Build the SQL expression for the status CHECK constraint."""
    values = ", ".join(f"'{s}'" for s in statuses)
    return f"status IN ({values})"


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # Step 1: Pre-condition check — abort if any legacy pipeline statuses exist
    # ------------------------------------------------------------------
    result = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM contacts "
            "WHERE status IN ('visit_scheduled', 'negotiation')"
        )
    )
    count = result.scalar_one()
    if count > 0:
        raise RuntimeError(
            f"Migration 018 aborted: {count} contact(s) have status "
            "'visit_scheduled' or 'negotiation'. "
            "Migrate them manually to a valid v17 status first."
        )

    # ------------------------------------------------------------------
    # Step 2: Expand CHECK constraint — allow both old and new states
    # ------------------------------------------------------------------
    op.drop_constraint("contacts_status_check", "contacts", type_="check")
    op.create_check_constraint(
        "contacts_status_check",
        "contacts",
        _make_check(_TRANSITION_STATUSES),
    )

    # ------------------------------------------------------------------
    # Step 3: Capture IDs to migrate for traceability, then run UPDATE
    # ------------------------------------------------------------------
    # Capture contact IDs before updating so we can write precise lead_events
    bind.execute(
        sa.text(
            "CREATE TEMPORARY TABLE _migration_018_contacted AS "
            "SELECT id FROM contacts WHERE status = 'contacted'"
        )
    )

    bind.execute(
        sa.text(
            "UPDATE contacts SET status = 'bot_replied' WHERE status = 'contacted'"
        )
    )

    # Write one lead_event per migrated contact for full traceability
    bind.execute(
        sa.text(
            "INSERT INTO lead_events "
            "    (contact_id, event_type, old_status, new_status, triggered_by, created_at) "
            "SELECT id, 'status_change', 'contacted', 'bot_replied', "
            "       'system:migration_018', NOW() "
            "FROM _migration_018_contacted"
        )
    )

    bind.execute(sa.text("DROP TABLE _migration_018_contacted"))

    # ------------------------------------------------------------------
    # Step 4: Insert new bot_setting for IC autoreply reenviados toggle
    # ------------------------------------------------------------------
    bind.execute(
        sa.text(
            "INSERT INTO bot_settings (key, value, description, updated_at) "
            "VALUES ("
            "  'ic_autoreply_reenviados_enabled', "
            "  'false', "
            "  'Habilitar autoreply de IC para leads reenviados (v17)', "
            "  NOW()"
            ") "
            "ON CONFLICT (key) DO NOTHING"
        )
    )

    # ------------------------------------------------------------------
    # Step 5: Contract CHECK constraint to final states only
    # ------------------------------------------------------------------
    op.drop_constraint("contacts_status_check", "contacts", type_="check")
    op.create_check_constraint(
        "contacts_status_check",
        "contacts",
        _make_check(_FINAL_STATUSES),
    )

    # ------------------------------------------------------------------
    # Step 6: Add agent_user_id column to contacts
    # ------------------------------------------------------------------
    op.add_column(
        "contacts",
        sa.Column(
            "agent_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Remove agent_user_id column
    op.drop_column("contacts", "agent_user_id")

    # Expand constraint for downgrade transition (allow both sets)
    op.drop_constraint("contacts_status_check", "contacts", type_="check")
    op.create_check_constraint(
        "contacts_status_check",
        "contacts",
        _make_check(_TRANSITION_STATUSES),
    )

    # NOTE: agent_replied is a v17 state that didn't exist before this migration.
    # Downgrading after agent_replied contacts accumulate will lose that distinction
    # (they revert to 'contacted'). Acceptable since downgrade is a dev-only escape hatch.
    # Revert bot_replied / agent_replied → contacted
    bind.execute(
        sa.text(
            "UPDATE contacts SET status = 'contacted' "
            "WHERE status IN ('bot_replied', 'agent_replied')"
        )
    )

    # Restore original constraint
    op.drop_constraint("contacts_status_check", "contacts", type_="check")
    op.create_check_constraint(
        "contacts_status_check",
        "contacts",
        _make_check(_ORIGINAL_STATUSES),
    )

    # Remove the bot_setting added by this migration
    bind.execute(
        sa.text(
            "DELETE FROM bot_settings "
            "WHERE key = 'ic_autoreply_reenviados_enabled'"
        )
    )

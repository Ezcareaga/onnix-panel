"""Seed WA template ContentSid placeholders in bot_settings.

Revision ID: 017
Revises: 016
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    templates = [
        ("wa_tpl_send_property", "PLACEHOLDER", "Template: envio propiedad especifica"),
        ("wa_tpl_send_preferences", "PLACEHOLDER", "Template: envio por preferencias de zona"),
        ("wa_tpl_send_generic", "PLACEHOLDER", "Template: contacto nuevo generico"),
        ("wa_tpl_followup", "PLACEHOLDER", "Template: follow-up (backlog, no implementar aun)"),
    ]
    for key, value, description in templates:
        bind.execute(
            sa.text(
                "INSERT INTO bot_settings (key, value, description) "
                "VALUES (:key, :value, :description) "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {"key": key, "value": value, "description": description},
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM bot_settings WHERE key IN ("
            "'wa_tpl_send_property', 'wa_tpl_send_preferences', "
            "'wa_tpl_send_generic', 'wa_tpl_followup')"
        )
    )

"""Refactor bot_settings: remove wizard/old templates, add v7 templates.

Revision ID: 020
Revises: 019
Create Date: 2026-04-15

Removes all wizard-era and obsolete WhatsApp template keys from bot_settings.
Adds the three template keys required by the v7 conversation flow.

Deleted keys:
- wa_tpl_ic_welcome_v2_pending, wa_tpl_ic_reenviado_welcome_pending
- wa_tpl_send_property_v2_pending
- wa_tpl_wizard_op, wa_tpl_wizard_zona, wa_tpl_elegir_zona
- wa_tpl_wizard_tipo, wa_tpl_wizard_tipo_compra, wa_tpl_wizard_tipo_alquiler
- wa_tpl_multi_barrio, wa_tpl_zona_casa, wa_tpl_zona_casa-duplex
- wa_tpl_zona_departamento, wa_tpl_zona_departamento-en-pozo, wa_tpl_zona_local
- wa_tpl_zona_terreno, wa_tpl_sin_mas, wa_tpl_no_res, wa_tpl_visita, wa_tpl_res1
- template_property_alert

Added keys:
- wa_tpl_lead (onnix_post_asesor — confirmed approved)
- wa_tpl_opt_out (plain text, no template)
- wa_tpl_followup_72h (HXe5f634b70acdc38f2223814c9cefd72b — submitted to Meta 2026-04-15, pending approval)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None

# Keys being removed in this migration
_DELETED_KEYS = [
    "wa_tpl_ic_welcome_v2_pending",
    "wa_tpl_ic_reenviado_welcome_pending",
    "wa_tpl_send_property_v2_pending",
    "wa_tpl_wizard_op",
    "wa_tpl_wizard_zona",
    "wa_tpl_elegir_zona",
    "wa_tpl_wizard_tipo",
    "wa_tpl_wizard_tipo_compra",
    "wa_tpl_wizard_tipo_alquiler",
    "wa_tpl_multi_barrio",
    "wa_tpl_zona_casa",
    "wa_tpl_zona_casa-duplex",
    "wa_tpl_zona_departamento",
    "wa_tpl_zona_departamento-en-pozo",
    "wa_tpl_zona_local",
    "wa_tpl_zona_terreno",
    "wa_tpl_sin_mas",
    "wa_tpl_no_res",
    "wa_tpl_visita",
    "wa_tpl_res1",
    "template_property_alert",
]

# Keys being inserted in this migration
_INSERTED_KEYS = [
    (
        "wa_tpl_lead",
        "HX90d98a7b2137c55105d10e3737754fec",
        "Template para confirmación de lead (onnix_post_asesor)",
    ),
    (
        "wa_tpl_opt_out",
        "",
        "Opt-out es texto plano, sin template",
    ),
    (
        "wa_tpl_followup_72h",
        "HXe5f634b70acdc38f2223814c9cefd72b",
        "Template 72h followup (onnix_followup_72h) — pendiente aprobacion Meta, aprobado 2026-04-15",
    ),
]


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Remove obsolete wizard/old template keys
    placeholders = ", ".join(f"'{k}'" for k in _DELETED_KEYS)
    bind.execute(
        text(f"DELETE FROM bot_settings WHERE key IN ({placeholders})")
    )

    # 2. Insert new v7 template keys
    for key, value, description in _INSERTED_KEYS:
        bind.execute(
            sa.text(
                "INSERT INTO bot_settings (key, value, description, updated_at) "
                "VALUES (:key, :value, :description, NOW()) "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {"key": key, "value": value, "description": description},
        )


def downgrade() -> None:
    bind = op.get_bind()

    # 1. Remove the newly inserted keys
    new_keys = ", ".join(f"'{k}'" for k, _, _ in _INSERTED_KEYS)
    bind.execute(
        text(f"DELETE FROM bot_settings WHERE key IN ({new_keys})")
    )

    # 2. Re-insert the deleted keys with placeholder values
    for key in _DELETED_KEYS:
        bind.execute(
            sa.text(
                "INSERT INTO bot_settings (key, value, description, updated_at) "
                "VALUES (:key, 'PLACEHOLDER', 'Restored by downgrade from 020', NOW()) "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {"key": key},
        )

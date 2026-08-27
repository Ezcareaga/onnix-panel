"""Seed 10 new WhatsApp template keys for M3 (Onnix identity, v3/v4).

Revision ID: 032
Revises: 031
Create Date: 2026-04-19

Fase H.1 of M3 — Tests Canónicos + Submit Meta Templates.

Inserts 10 new bot_settings rows with value='PLACEHOLDER'.  The real
ContentSids are filled in by `scripts/twilio_update_m3_sids.py` after
Meta approves each template (typically 24-48h after submit).

All 10 templates will be submitted as category=MARKETING, language=es.
The submit is performed manually by Ez via `scripts/twilio_create_templates_m3.py`.

New keys:
  - wa_tpl_ic_welcome_v3               → IC lead directo con match de propiedad
  - wa_tpl_ic_reenviado_welcome_v3     → IC lead reenviado (sin match exacto)
  - wa_tpl_send_property_v4            → Asesor envía propiedad desde panel
  - wa_tpl_send_preferences_v4         → Asesor envía por preferencias del contacto
  - wa_tpl_send_generic_v3             → Contacto nuevo sin contexto de propiedad
  - wa_tpl_followup_v3                 → Follow-up a las 24h
  - wa_tpl_followup_72h_v3             → Follow-up a las 72h
  - wa_tpl_agent_reply_v3              → Reactivación por asesor
  - wa_tpl_ic_recurrente_directo_v2    → IC recurrente directo
  - wa_tpl_ic_recurrente_reenviado_v2  → IC recurrente reenviado

Existing 9 active templates are NOT touched — they remain live until Ez
decides to cut them once the v3/v4 SIDs are approved and deployed.

Idempotent: INSERT ... ON CONFLICT (key) DO NOTHING — safe to re-run.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


# Keys introduced by this migration.
# Exposed at module level so tests can import without duplicating the list.
M3_TEMPLATE_KEYS: list[tuple[str, str]] = [
    (
        "wa_tpl_ic_welcome_v3",
        "IC lead directo — cliente consulta propiedad en InfoCasas, hay match (v3 Onnix)",
    ),
    (
        "wa_tpl_ic_reenviado_welcome_v3",
        "IC lead reenviado — cliente consulta en IC, sin match exacto (v3 Onnix)",
    ),
    (
        "wa_tpl_send_property_v4",
        "Asesor envía propiedad específica desde el panel al contacto (v4 Onnix)",
    ),
    (
        "wa_tpl_send_preferences_v4",
        "Asesor envía propiedades según preferencias del contacto desde el panel (v4 Onnix)",
    ),
    (
        "wa_tpl_send_generic_v3",
        "Contacto nuevo sin contexto de propiedad — apertura de conversación (v3 Onnix)",
    ),
    (
        "wa_tpl_followup_v3",
        "Follow-up automático a las 24h sin respuesta del contacto (v3 Onnix)",
    ),
    (
        "wa_tpl_followup_72h_v3",
        "Follow-up automático a las 72h sin respuesta del contacto (v3 Onnix)",
    ),
    (
        "wa_tpl_agent_reply_v3",
        "Reactivación de conversación por asesor tras silencio del bot (v3 Onnix)",
    ),
    (
        "wa_tpl_ic_recurrente_directo_v2",
        "Cliente recurrente — nueva consulta IC con match directo (v2 Onnix)",
    ),
    (
        "wa_tpl_ic_recurrente_reenviado_v2",
        "Cliente recurrente — nueva consulta IC reenviada sin match exacto (v2 Onnix)",
    ),
]

_UPGRADE_SQL = (
    "INSERT INTO bot_settings (key, value, description, updated_at) "
    "VALUES (:key, 'PLACEHOLDER', :description, NOW()) "
    "ON CONFLICT (key) DO NOTHING"
)

_DOWNGRADE_SQL = (
    "DELETE FROM bot_settings WHERE key IN ({placeholders})"
)


def upgrade() -> None:
    bind = op.get_bind()
    for key, description in M3_TEMPLATE_KEYS:
        bind.execute(
            sa.text(_UPGRADE_SQL),
            {"key": key, "description": description},
        )


def downgrade() -> None:
    bind = op.get_bind()
    keys = [k for k, _ in M3_TEMPLATE_KEYS]
    placeholders = ", ".join(f"'{k}'" for k in keys)
    bind.execute(sa.text(_DOWNGRADE_SQL.format(placeholders=placeholders)))

"""Drop dead WhatsApp template rows from bot_settings.

Revision ID: 025
Revises: 024
Create Date: 2026-04-17

Fase 7 of business-logic-cleanup removes five WhatsApp template keys that
are no longer referenced by any live code in panel/app/.  All five keys
were confirmed dead by grep audit before this migration was written.

Deleted keys:
- wa_tpl_ambiguo_visita  — wizard-era ambiguous-visit template (dead post-v7)
- wa_tpl_conv            — conversacion template (dead post-v7)
- wa_tpl_lead            — onnix_post_asesor template (superseded, not called)
- wa_tpl_post_asesor     — post-asesor with follow-up buttons (dead post-v7)
- wa_tpl_saludo          — saludo with Buscar/Asesor buttons (dead post-v7)

NOTE: wa_tpl_opt_out is intentionally NOT deleted — it is active post-Fase-6.

Reference:
  - Fase 7 plan (business-logic-cleanup): drop dead wa template rows
  - panel/tests/bot/test_response_builder.py:939,1135 — assertions confirm
    wa_tpl_conv / wa_tpl_saludo are NOT used (B2 cleanup, consistent).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None

# Captured prod values for safe downgrade restore.
# Tuple layout: (key, value, description)
# wa_tpl_ambiguo_visita description is NULL in prod (confirmed via direct DB query).
_DELETED_ROWS: list[tuple[str, str, str | None]] = [
    ("wa_tpl_ambiguo_visita", "HXc2cde6e12536ca2082688dc7041aec16", None),
    ("wa_tpl_conv",           "HXc2cde6e12536ca2082688dc7041aec16", "ContentSid: conversacion con Buscar/Asesor"),
    ("wa_tpl_lead",           "HX90d98a7b2137c55105d10e3737754fec", "Template para confirmación de lead (onnix_post_asesor)"),
    ("wa_tpl_post_asesor",    "HX90d98a7b2137c55105d10e3737754fec", "ContentSid: post-asesor con Seguir buscando"),
    ("wa_tpl_saludo",         "HXe9da759534dd2cf4990b22909f7ec36e", "ContentSid: saludo con botones Buscar/Asesor"),
]


def upgrade() -> None:
    bind = op.get_bind()
    for key, _, _ in _DELETED_ROWS:
        bind.execute(
            sa.text("DELETE FROM bot_settings WHERE key = :key"),
            {"key": key},
        )


def downgrade() -> None:
    bind = op.get_bind()
    for key, value, description in _DELETED_ROWS:
        bind.execute(
            sa.text(
                "INSERT INTO bot_settings (key, value, description, updated_at) "
                "VALUES (:key, :value, :description, NOW()) "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {"key": key, "value": value, "description": description},
        )

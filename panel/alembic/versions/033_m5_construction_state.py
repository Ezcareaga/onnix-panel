"""Add construction_state column to properties + backfill + seed M5 feature flags.

Revision ID: 033_m5_construction_state
Revises: 032
Create Date: 2026-04-20

Fase B of M5 — Intelligent Search.

Adds a structured `construction_state` column to `properties` with a CHECK
constraint allowing the four canonical values or NULL.  A heuristic backfill
populates existing active rows ordered by signal strength (strongest first so
later passes only touch still-NULL rows):

  1. property_type ILIKE '%en-pozo%'       → 'en_pozo'   (Onnixpy semi-structured)
  2. title/description 'en pozo'/'preventa' → 'en_pozo'
  3. title/description 'en construcci%'     → 'en_construccion'
  4. title/description 'a estrenar'/'recien terminado' → 'a_estrenar'

Rows without signal remain NULL (intentional — marking everything 'terminado'
would generate too many false positives).

Seeds two M5 feature flags in bot_settings (value='false' — activation is
manual post go/no-go in Fase K):
  - m5_zero_results_alternatives_enabled
  - m5_construction_state_filter_enabled

Idempotent:
  - All UPDATE passes filter WHERE construction_state IS NULL → safe to re-run.
  - Seed uses ON CONFLICT (key) DO NOTHING → safe to re-run.

Down revision restores the schema to exactly its pre-033 state.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "033_m5_construction_state"
down_revision = "032"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Feature flag seeds (exported for drift guard in tests)
# ---------------------------------------------------------------------------
M5_FLAG_KEYS: list[tuple[str, str]] = [
    (
        "m5_zero_results_alternatives_enabled",
        "M5: activa AlternativesBuilder cuando búsqueda retorna 0 resultados",
    ),
    (
        "m5_construction_state_filter_enabled",
        "M5: usa columna construction_state en sql_filters en lugar de ILIKE",
    ),
]

_SEED_SQL = (
    "INSERT INTO bot_settings (key, value, description, updated_at) "
    "VALUES (:key, 'false', :description, NOW()) "
    "ON CONFLICT (key) DO NOTHING"
)

_UNSEED_SQL = (
    "DELETE FROM bot_settings WHERE key IN ({placeholders})"
)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Add column
    # ------------------------------------------------------------------
    op.add_column(
        "properties",
        sa.Column("construction_state", sa.String(20), nullable=True),
    )

    # ------------------------------------------------------------------
    # 2. CHECK constraint — 4 valid values + NULL
    # ------------------------------------------------------------------
    op.create_check_constraint(
        "ck_properties_construction_state",
        "properties",
        (
            "construction_state IN "
            "('en_pozo','en_construccion','a_estrenar','terminado') "
            "OR construction_state IS NULL"
        ),
    )

    # ------------------------------------------------------------------
    # 3. Index for flag-gated exact-match filter in sql_filters.py
    # ------------------------------------------------------------------
    op.create_index(
        "ix_properties_construction_state",
        "properties",
        ["construction_state"],
    )

    # ------------------------------------------------------------------
    # 4. Heuristic backfill — ordered by signal strength.
    #    Each pass only touches rows still NULL (idempotent).
    #    All passes filter is_active=TRUE (inactive rows left alone).
    # ------------------------------------------------------------------

    bind = op.get_bind()

    # Pass 1: Onnixpy semi-structured property_type field (strongest signal)
    bind.execute(
        sa.text(
            "UPDATE properties "
            "SET construction_state = 'en_pozo' "
            "WHERE is_active = TRUE "
            "  AND construction_state IS NULL "
            "  AND property_type ILIKE '%en-pozo%'"
        )
    )

    # Pass 2: free-text "en pozo" / "preventa" in title or description
    bind.execute(
        sa.text(
            "UPDATE properties "
            "SET construction_state = 'en_pozo' "
            "WHERE is_active = TRUE "
            "  AND construction_state IS NULL "
            "  AND ("
            "    title ILIKE '%en pozo%' OR description ILIKE '%en pozo%' "
            "    OR title ILIKE '%preventa%' OR description ILIKE '%preventa%'"
            "  )"
        )
    )

    # Pass 3: "en construccion" / "en construcción" (ILIKE covers both)
    bind.execute(
        sa.text(
            "UPDATE properties "
            "SET construction_state = 'en_construccion' "
            "WHERE is_active = TRUE "
            "  AND construction_state IS NULL "
            "  AND ("
            "    title ILIKE '%en construcci%' OR description ILIKE '%en construcci%'"
            "  )"
        )
    )

    # Pass 4: "a estrenar" / "recien terminado"
    bind.execute(
        sa.text(
            "UPDATE properties "
            "SET construction_state = 'a_estrenar' "
            "WHERE is_active = TRUE "
            "  AND construction_state IS NULL "
            "  AND ("
            "    title ILIKE '%a estrenar%' OR description ILIKE '%a estrenar%' "
            "    OR title ILIKE '%recien terminado%' OR description ILIKE '%recien terminado%'"
            "  )"
        )
    )

    # ------------------------------------------------------------------
    # 5. Seed M5 feature flags (both OFF by default)
    # ------------------------------------------------------------------
    for key, description in M5_FLAG_KEYS:
        bind.execute(
            sa.text(_SEED_SQL),
            {"key": key, "description": description},
        )


def downgrade() -> None:
    bind = op.get_bind()

    # Remove seed flags
    keys = [k for k, _ in M5_FLAG_KEYS]
    placeholders = ", ".join(f"'{k}'" for k in keys)
    bind.execute(sa.text(_UNSEED_SQL.format(placeholders=placeholders)))

    # Drop index before constraint, constraint before column
    op.drop_index("ix_properties_construction_state", table_name="properties")
    op.drop_constraint("ck_properties_construction_state", "properties", type_="check")
    op.drop_column("properties", "construction_state")

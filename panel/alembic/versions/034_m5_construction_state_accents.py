"""M5 — fix accent variants en backfill construction_state.

Revision ID: 034_m5_accents
Revises: 033_m5_construction_state
Create Date: 2026-04-21

Captura rows con 'recién terminado' / 'recién construido' etc que quedaron
NULL tras la migración 033 por usar ILIKE sin unaccent.

Usa f_unaccent(lower(...)) para matchear con acentos. Idempotente — todos los
UPDATE filtran WHERE construction_state IS NULL, de modo que no sobreescriben
valores ya asignados.

Precondicción: f_unaccent() debe existir en la DB (creada por migración previa
junto con la extensión unaccent, que el proyecto usa para búsquedas).
"""
from __future__ import annotations

from alembic import op

revision = "034_m5_accents"
down_revision = "033_m5_construction_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pass 1: en_pozo — acento no aplica, pero incluimos preventa por completitud
    # con normalización consistente. Solo toca filas donde sigue NULL.
    op.execute("""
        UPDATE properties SET construction_state = 'en_pozo'
        WHERE is_active = TRUE AND construction_state IS NULL
          AND (
            f_unaccent(lower(title))       LIKE '%en pozo%'
            OR f_unaccent(lower(description)) LIKE '%en pozo%'
            OR f_unaccent(lower(title))       LIKE '%preventa%'
            OR f_unaccent(lower(description)) LIKE '%preventa%'
          );
    """)

    # Pass 2: en_construccion — captura 'construcción' con acento
    op.execute("""
        UPDATE properties SET construction_state = 'en_construccion'
        WHERE is_active = TRUE AND construction_state IS NULL
          AND (
            f_unaccent(lower(title))       LIKE '%en construccion%'
            OR f_unaccent(lower(description)) LIKE '%en construccion%'
          );
    """)

    # Pass 3: a_estrenar — captura 'recién terminado/a' con acento
    # IMPORTANTE: este pase va ANTES que cualquier pase de "terminado" para
    # que "recién terminado" no quede clasificado erróneamente.
    op.execute("""
        UPDATE properties SET construction_state = 'a_estrenar'
        WHERE is_active = TRUE AND construction_state IS NULL
          AND (
            f_unaccent(lower(title))       LIKE '%a estrenar%'
            OR f_unaccent(lower(description)) LIKE '%a estrenar%'
            OR f_unaccent(lower(title))       LIKE '%recien terminado%'
            OR f_unaccent(lower(description)) LIKE '%recien terminado%'
            OR f_unaccent(lower(title))       LIKE '%recien terminada%'
            OR f_unaccent(lower(description)) LIKE '%recien terminada%'
          );
    """)


def downgrade() -> None:
    # No se puede deshacer selectivamente: no hay forma de saber qué rows
    # matcheó esta migración vs 033. Se deja como no-op documentado.
    # Para revertir completamente M5 construction_state, usar 033.downgrade().
    pass

"""visits table + CHECK widen contacts.status with 'visit_scheduled'

Revision ID: 040_visits
Revises: 039_roles_auth_audit
Create Date: 2026-05-27

M6.2 — visit_scheduled + Tabla Visits (Phase 115-01).
Spec: .planning/phases/114-m6.2-plan-visits/114-01-PLAN.md §1.
Decisions: .planning/phases/114-m6.2-plan-visits/114-CONTEXT.md (OQ-1..OQ-8).

Cambios (upgrade):
  1. Pre-condition guard — abortar si contacts.status tiene valores fuera del enum post-018.
  2. CHECK contacts.status expand: post-018 enum + 'visit_scheduled'.
  3. CREATE OR REPLACE FUNCTION trigger_set_updated_at() — idempotente (4 tablas ya la usan;
     en DBs frescas de test sin schema.sql, asegura disponibilidad).
  4. CREATE TABLE visits (VISIT-02): id, contact_id (FK CASCADE), property_id (FK SET NULL),
     agent_user_id (FK RESTRICT), scheduled_at, status (DEFAULT 'scheduled', CHECK 4 valores),
     source (DEFAULT 'panel', CHECK 3 valores), notes, created_at, updated_at.
  5. 3 índices (VISIT-03):
     - idx_visits_contact (contact_id, scheduled_at DESC)
     - idx_visits_scheduled (scheduled_at) WHERE status='scheduled'
     - idx_visits_agent (agent_user_id, scheduled_at) WHERE status='scheduled'
  6. Trigger set_updated_at BEFORE UPDATE ON visits.

Downgrade:
  - Pre-condition guard: abortar si hay contacts con status='visit_scheduled'.
  - Drop simétrico (trigger → índices → tabla → CHECK contracted).
  - NO drop trigger_set_updated_at() function: 4 tablas más dependen de ella.
  - Pérdida aceptable en downgrade: TODO el historial de visits (dev-only escape hatch).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "040_visits"
down_revision = "039_roles_auth_audit"
branch_labels = None
depends_on = None


# Estados finales de contacts post-M6.1 (heredados del fixture mig 018).
_CONTACT_FINAL_STATUSES = (
    "new", "bot_replied", "agent_replied", "interested",
    "closed", "no_response", "discarded", "deleted",
)

# Estado final post-mig-040 (M6.2): reintroducimos 'visit_scheduled'.
_CONTACT_FINAL_STATUSES_M6_2 = _CONTACT_FINAL_STATUSES + ("visit_scheduled",)

# Estados válidos para una fila de visits.status.
_VISIT_STATUSES = ("scheduled", "done", "cancelled", "no_show")

# Valores válidos para visits.source.
_VISIT_SOURCES = ("panel", "bot", "manual")


def _make_check(statuses: tuple[str, ...]) -> str:
    """Build the SQL expression for a status-style CHECK constraint.
    Pattern from mig 018:45-48.
    """
    values = ", ".join(f"'{s}'" for s in statuses)
    return f"status IN ({values})"


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # Step 1 — Pre-condition guard (defensive, mirror mig 018:51-69)
    # Asegurar que contacts.status solo contiene valores del CHECK vigente
    # (no debería haber 'visit_scheduled' pre-040 — fue removido en mig 018).
    #
    # NOTA — deviación intencional de 114-RESEARCH §7.1: la matriz de research
    # recomienda "NO guard" en mig 040 (es additive, no hay rows que limpiar).
    # Plan 114 sobre-protege: el guard es no-op si prod está limpia (que debe
    # estarlo, post-mig-018) y barato si no. Mantener este guard preserva la
    # simetría con mig 018:51-69 y atrapa derivas inesperadas si alguien
    # bypaseó el CHECK previamente. Phase 115 executor: do NOT remove.
    # ------------------------------------------------------------------
    result = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM contacts "
            "WHERE status NOT IN " + str(_CONTACT_FINAL_STATUSES)
        )
    )
    out_of_check = result.scalar_one()
    if out_of_check > 0:
        raise RuntimeError(
            f"Migration 040 aborted: {out_of_check} contact(s) have status outside "
            f"the post-018 enum {_CONTACT_FINAL_STATUSES}. Reconciliar antes de aplicar."
        )

    # ------------------------------------------------------------------
    # Step 2 — CHECK widen: estado final post-mig-040 incluye 'visit_scheduled'.
    # Patrón mig 018:73-79 / mig 039:66-71.
    # ------------------------------------------------------------------
    op.drop_constraint("contacts_status_check", "contacts", type_="check")
    op.create_check_constraint(
        "contacts_status_check",
        "contacts",
        _make_check(_CONTACT_FINAL_STATUSES_M6_2),
    )

    # ------------------------------------------------------------------
    # Step 3 — Reuse / re-declare trigger_set_updated_at() function.
    #
    # CRITICAL (OQ-9 in 114-RESEARCH §4): la función YA EXISTE en prod+staging
    # con el nombre `trigger_set_updated_at` (creada por `scripts/schema.sql`,
    # NO por Alembic). Sin embargo, la auditoría 113 buscó SOLO en
    # `panel/alembic/versions/` (0 hits) y CONTEXT §5.1 erradamente afirmó que
    # "NO existe".
    #
    # Decision (Plan 114): usar `CREATE OR REPLACE FUNCTION` con el nombre
    # existente (`trigger_set_updated_at`). En prod/staging es no-op idempotente;
    # en DBs frescas de test que no aplicaron `scripts/schema.sql`, asegura
    # disponibilidad. NUNCA crear una segunda función paralela `set_updated_at()`
    # — sería un duplicate del mismo body y un footgun a futuro.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION trigger_set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
          NEW.updated_at = NOW();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # ------------------------------------------------------------------
    # Step 4 — Crear tabla visits (VISIT-02).
    # Schema literal per ROADMAP §M6.2 (docs/ROADMAP_ONNIX_v8_v10.md:453-467)
    # + REQUIREMENTS.md VISIT-02.
    #
    # FK ondelete:
    #   - contact_id  ON DELETE CASCADE — borrar contacto borra su historial de
    #     visitas (la fila contact ES el agregado).
    #   - property_id ON DELETE SET NULL — propiedad puede irse (is_active=FALSE
    #     no borra fisicamente, pero defensivo).
    #   - agent_user_id ON DELETE RESTRICT — simétrico a contacts.agent_user_id
    #     (mig 039 D-1). Los users nunca se borran físicamente (CLAUDE.md regla
    #     "nunca DROP TABLE con datos"); is_active=FALSE en lugar. RESTRICT
    #     bloquea el intento explícitamente, mejor que CASCADE (perdería historia)
    #     o SET NULL (perdería atribución).
    # ------------------------------------------------------------------
    op.create_table(
        "visits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("property_id", sa.Integer(), nullable=True),
        sa.Column("agent_user_id", sa.Integer(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="scheduled",
        ),
        sa.Column(
            "source",
            sa.String(length=30),
            nullable=False,
            server_default="panel",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], ondelete="CASCADE",
            name="visits_contact_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["property_id"], ["properties.id"], ondelete="SET NULL",
            name="visits_property_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["agent_user_id"], ["users.id"], ondelete="RESTRICT",
            name="visits_agent_user_id_fkey",
        ),
        sa.CheckConstraint(
            f"status IN ({', '.join(repr(s) for s in _VISIT_STATUSES)})",
            name="visits_status_check",
        ),
        sa.CheckConstraint(
            f"source IN ({', '.join(repr(s) for s in _VISIT_SOURCES)})",
            name="visits_source_check",
        ),
    )

    # ------------------------------------------------------------------
    # Step 5 — Índices (VISIT-03). 3 índices per ROADMAP §M6.2.
    #
    # 1. (contact_id, scheduled_at DESC) — query "todas las visits de un contact"
    #    ordenadas (UI tabla bloque Visitas).
    # 2. (scheduled_at) WHERE status='scheduled' — query "próximas visitas
    #    activas" (M11 reminders cron, hoy futuro).
    # 3. (agent_user_id, scheduled_at) WHERE status='scheduled' — query "visitas
    #    activas de un agente" (M8 notifications, hoy futuro).
    # ------------------------------------------------------------------
    op.create_index(
        "idx_visits_contact",
        "visits",
        ["contact_id", sa.text("scheduled_at DESC")],
    )
    op.create_index(
        "idx_visits_scheduled",
        "visits",
        ["scheduled_at"],
        postgresql_where=sa.text("status = 'scheduled'"),
    )
    op.create_index(
        "idx_visits_agent",
        "visits",
        ["agent_user_id", "scheduled_at"],
        postgresql_where=sa.text("status = 'scheduled'"),
    )

    # ------------------------------------------------------------------
    # Step 6 — Trigger set_updated_at sobre visits (VISIT-03).
    # Patrón scripts/schema.sql:per-table pattern (sin idempotency guard:
    # el CREATE TRIGGER falla si la tabla acaba de crearse y el trigger no
    # existe, lo cual es el caso aquí — Step 4 creó la tabla).
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TRIGGER set_updated_at
          BEFORE UPDATE ON visits
          FOR EACH ROW
          EXECUTE FUNCTION trigger_set_updated_at();
        """
    )


def downgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # Step 1 — Pre-condition guard: abortar si hay contactos en 'visit_scheduled'
    # (no podemos contraer el CHECK con rows ilegales). Patrón mig 039:163-175.
    # ------------------------------------------------------------------
    result = bind.execute(
        sa.text("SELECT COUNT(*) FROM contacts WHERE status = 'visit_scheduled'")
    )
    n_vs = result.scalar_one()
    if n_vs > 0:
        raise RuntimeError(
            f"Migration 040 downgrade aborted: {n_vs} contact(s) with status='visit_scheduled'. "
            "Antes de bajar la migración, reasigná esos contacts a otro status. "
            "Sugerencia: UPDATE contacts SET status='interested' WHERE status='visit_scheduled'; "
            "(o el status manual que corresponda — visit_scheduled era un flag de pipeline)."
        )

    # ------------------------------------------------------------------
    # Step 2 — Drop trigger sobre visits (Step 6 inverse).
    # ------------------------------------------------------------------
    op.execute("DROP TRIGGER IF EXISTS set_updated_at ON visits;")

    # ------------------------------------------------------------------
    # Step 3 — Drop índices (Step 5 inverse).
    # ------------------------------------------------------------------
    op.drop_index("idx_visits_agent", table_name="visits")
    op.drop_index("idx_visits_scheduled", table_name="visits")
    op.drop_index("idx_visits_contact", table_name="visits")

    # ------------------------------------------------------------------
    # Step 4 — Drop tabla visits (Step 4 inverse).
    # ATENCIÓN: pierde TODO el historial de visits (audit trail completo).
    # Aceptable en downgrade (dev-only escape hatch). Si se quisiera preservar:
    #   pg_dump -t visits onnix_dev > visits_backup.sql
    # ------------------------------------------------------------------
    op.drop_table("visits")

    # ------------------------------------------------------------------
    # Step 5 — NO dropear trigger_set_updated_at() function — la usan 4 tablas
    # más (properties, contacts, conversations, users). Patrón OQ-9 §4.4 Plan 114.
    # La función queda; es no-op en prod/staging porque CREATE OR REPLACE no
    # creó nada nuevo en Step 3 del upgrade.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Step 6 — Contraer CHECK contacts.status a estado pre-040 (sin
    # 'visit_scheduled'). Patrón mig 018:177-182.
    # ------------------------------------------------------------------
    op.drop_constraint("contacts_status_check", "contacts", type_="check")
    op.create_check_constraint(
        "contacts_status_check",
        "contacts",
        _make_check(_CONTACT_FINAL_STATUSES),
    )

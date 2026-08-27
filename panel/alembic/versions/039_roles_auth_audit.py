"""roles + auth_audit + indices + restrict FK

Revision ID: 039_roles_auth_audit
Revises: 038_seed_chatbot_flag
Create Date: 2026-05-24

M6.1 — Roles + Login Hardening (Phase 111-01).
Spec: .planning/phases/110-m6.1-plan-roles-auth/110-01-PLAN.md §1, §2, §6.4, §10.1.

Cambios (upgrade):
  1. Pre-condition guard — abortar si users.role contiene valores fuera del
     enum vigente ('admin','user') (defensive — mig 018:51-69 pattern).
  2. CHECK users.role expand: ('admin','user') → ('admin','agent','user').
  3. Nueva tabla auth_audit (id, email, ip, user_agent, result, created_at)
     + CHECK result IN (5 valores).
  4. Índice idx_auth_audit_email_created_desc (email, created_at DESC) —
     usado por el lockout check (ROLE-04).
  5. Índice parcial idx_contacts_agent_user_id WHERE agent_user_id IS NOT NULL
     (ROLE-14 — sparsity 99.98% NULL).
  6. FK contacts.agent_user_id_fkey → ON DELETE RESTRICT explícito (D-1).
  7. Columna contacts.agent_seen_at TIMESTAMPTZ NULL (ROLE-15) — badge "nuevo".
  8. Columna contacts.agent_assigned_at TIMESTAMPTZ NULL (ROLE-15, §10.1) —
     setteada por endpoint agent-assign en Phase 111-03.

Downgrade:
  - Pre-condition guard — abortar si users.role='agent' existe (CHECK contract
    rechazaría las filas; debe reasignarse manualmente).
  - Drop simétrico. Pérdida aceptable: auth_audit log (security log, no business
    data) + agent_seen_at/agent_assigned_at timestamps (UI state).
  - Business data NUNCA se pierde en el downgrade.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "039_roles_auth_audit"
down_revision = "038_seed_chatbot_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # Step 1 — Pre-condition guard (patrón mig 018:51-69)
    # Asegurar que users.role solo contiene valores del CHECK vigente.
    # ------------------------------------------------------------------
    result = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM users WHERE role NOT IN ('admin', 'user')"
        )
    )
    out_of_check = result.scalar_one()
    if out_of_check > 0:
        raise RuntimeError(
            f"Migration 039 aborted: {out_of_check} user(s) have role outside "
            "('admin', 'user'). Reconciliar antes de aplicar."
        )

    # ------------------------------------------------------------------
    # Step 2 — CHECK expand a estado final: ('admin','agent','user')
    # No hay migración de datos: solo se AGREGA 'agent'.
    # ------------------------------------------------------------------
    op.drop_constraint("users_role_check", "users", type_="check")
    op.create_check_constraint(
        "users_role_check",
        "users",
        "role IN ('admin', 'agent', 'user')",
    )

    # ------------------------------------------------------------------
    # Step 3 — Crear tabla auth_audit (ROLE-02)
    # ------------------------------------------------------------------
    op.create_table(
        "auth_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "result IN ('success', 'wrong_password', 'inactive', 'not_found', 'locked')",
            name="auth_audit_result_check",
        ),
    )

    # ------------------------------------------------------------------
    # Step 4 — Índice descending sobre (email, created_at)
    # Justificación: el lockout check filtra por email + mira la ventana
    # reciente (created_at DESC).
    # ------------------------------------------------------------------
    op.create_index(
        "idx_auth_audit_email_created_desc",
        "auth_audit",
        ["email", sa.text("created_at DESC")],
    )

    # ------------------------------------------------------------------
    # Step 5 — Índice parcial sobre contacts.agent_user_id (ROLE-14)
    # SUMMARY §14 Q4: 2 contacts assigned / 11314 NULL → 99.98% sparsity.
    # ------------------------------------------------------------------
    op.create_index(
        "idx_contacts_agent_user_id",
        "contacts",
        ["agent_user_id"],
        postgresql_where=sa.text("agent_user_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # Step 6 — FK contacts.agent_user_id → users.id with ON DELETE RESTRICT (D-1)
    # Cambio: NO ACTION (default, confdeltype='a') → RESTRICT (confdeltype='r').
    # Patrón operativo: users nunca se borran físicamente, solo
    # is_active=FALSE. Si admin intentara borrar un user con leads asignados,
    # RESTRICT lo bloquea explícitamente (más temprano que NO ACTION).
    # ------------------------------------------------------------------
    op.drop_constraint(
        "contacts_agent_user_id_fkey",
        "contacts",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "contacts_agent_user_id_fkey",
        "contacts",
        "users",
        ["agent_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # ------------------------------------------------------------------
    # Step 7 — agent_seen_at column (ROLE-15)
    # Setteada por Phase 111-07 cuando el agent abre el contact.
    # Badge "nuevo" visible mientras agent_assigned_at > agent_seen_at.
    # ------------------------------------------------------------------
    op.add_column(
        "contacts",
        sa.Column("agent_seen_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ------------------------------------------------------------------
    # Step 8 — agent_assigned_at column (ROLE-15, §10.1)
    # Setteada por Phase 111-03 al ejecutar agent-assign (func.now()).
    # ------------------------------------------------------------------
    op.add_column(
        "contacts",
        sa.Column("agent_assigned_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # Step 1 — Pre-condition guard: abort si hay users con role='agent'
    # (NO podemos contraer el CHECK con rows ilegales). Patrón mig 018:51-69.
    # ------------------------------------------------------------------
    result = bind.execute(
        sa.text("SELECT COUNT(*) FROM users WHERE role = 'agent'")
    )
    n_agents = result.scalar_one()
    if n_agents > 0:
        raise RuntimeError(
            f"Migration 039 downgrade aborted: {n_agents} user(s) with role='agent'. "
            "Reasigná esos usuarios a 'user' o 'admin' antes de bajar la migración. "
            "Sugerencia: UPDATE users SET role='user' WHERE role='agent'; "
            "(o eliminar las filas si corresponde, previo NULL en contacts.agent_user_id)."
        )

    # ------------------------------------------------------------------
    # Step 2 — Drop agent_assigned_at column (Step 8 inverse)
    # ------------------------------------------------------------------
    op.drop_column("contacts", "agent_assigned_at")

    # ------------------------------------------------------------------
    # Step 3 — Drop agent_seen_at column (Step 7 inverse)
    # ------------------------------------------------------------------
    op.drop_column("contacts", "agent_seen_at")

    # ------------------------------------------------------------------
    # Step 4 — Revertir FK a NO ACTION (Step 6 inverse)
    # ------------------------------------------------------------------
    op.drop_constraint(
        "contacts_agent_user_id_fkey",
        "contacts",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "contacts_agent_user_id_fkey",
        "contacts",
        "users",
        ["agent_user_id"],
        ["id"],
        # ondelete NO especificado → NO ACTION (default PG, estado pre-039).
    )

    # ------------------------------------------------------------------
    # Step 5 — Drop índice parcial (Step 5 inverse)
    # ------------------------------------------------------------------
    op.drop_index("idx_contacts_agent_user_id", table_name="contacts")

    # ------------------------------------------------------------------
    # Step 6 — Drop índice + tabla auth_audit (Steps 3+4 inverse)
    # ATENCIÓN: esto pierde el log de auth_audit. Aceptable (es log, no
    # business data). Si se quisiera preservar, dump previo:
    #   pg_dump -t auth_audit onnix_dev > auth_audit_backup.sql
    # ------------------------------------------------------------------
    op.drop_index("idx_auth_audit_email_created_desc", table_name="auth_audit")
    op.drop_table("auth_audit")

    # ------------------------------------------------------------------
    # Step 7 — Contraer CHECK a estado original ('admin','user') (Step 2 inverse)
    # ------------------------------------------------------------------
    op.drop_constraint("users_role_check", "users", type_="check")
    op.create_check_constraint(
        "users_role_check",
        "users",
        "role IN ('admin', 'user')",
    )

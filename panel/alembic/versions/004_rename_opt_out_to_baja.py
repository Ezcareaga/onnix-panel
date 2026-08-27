"""Rename opt_out to baja across contacts table

Revision ID: 004
Revises: 003
Create Date: 2026-03-02

Changes:
- contacts.status CHECK constraint: 'opt_out' → 'baja'
- contacts column: opted_out_at → baja_at
- Trigger: enforce_opt_out_terminal → enforce_baja_terminal
- Trigger function: prevent_opt_out_reversal → prevent_baja_reversal
- UPDATE contacts SET status = 'baja' WHERE status = 'opt_out'
"""
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def _ya_renombrado(bind) -> bool:
    """¿La base ya viene con el rename hecho?

    `scripts/schema.sql` es el baseline de este repo y trae adentro el estado
    POST-004: la columna `baja_at` y el trigger `enforce_baja_terminal`. El
    encabezado de ese archivo documenta el orden `schema.sql` →
    `alembic upgrade head`, y ese orden moria justo acá con

        trigger "enforce_opt_out_terminal" for table "contacts" does not exist

    porque el paso 1 apaga un trigger que en esa base nunca existió.
    `scripts/make_test_db.sh` esquivaba el problema armando la base de test con
    un `pg_dump` en vez de correr la cadena — pero eso necesita una base ya
    migrada, o sea que no servía para levantar el proyecto de cero.

    Con este guard 004 es idempotente y el orden documentado funciona de verdad.
    """
    return bool(
        bind.exec_driver_sql(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'contacts' AND column_name = 'baja_at')"
        ).scalar()
    )


def upgrade() -> None:
    if _ya_renombrado(op.get_bind()):
        return

    # 1. Update any existing rows with status='opt_out' → 'baja'
    #    Must disable trigger first since it prevents changes on opt_out rows
    op.execute("ALTER TABLE contacts DISABLE TRIGGER enforce_opt_out_terminal")
    op.execute("UPDATE contacts SET status = 'baja' WHERE status = 'opt_out'")
    op.execute("ALTER TABLE contacts ENABLE TRIGGER enforce_opt_out_terminal")

    # 2. Replace CHECK constraint: swap 'opt_out' for 'baja'
    op.execute("ALTER TABLE contacts DROP CONSTRAINT contacts_status_check")
    op.execute("""
        ALTER TABLE contacts ADD CONSTRAINT contacts_status_check
        CHECK (status IN ('new', 'contacted', 'hot', 'interview', 'cold', 'baja'))
    """)

    # 3. Rename column opted_out_at → baja_at
    op.execute("ALTER TABLE contacts RENAME COLUMN opted_out_at TO baja_at")

    # 4. Drop old trigger and function
    op.execute("DROP TRIGGER IF EXISTS enforce_opt_out_terminal ON contacts")
    op.execute("DROP FUNCTION IF EXISTS prevent_opt_out_reversal()")

    # 5. Create new trigger function with 'baja' references
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_baja_reversal()
        RETURNS TRIGGER AS $$
        BEGIN
          IF OLD.status = 'baja' AND NEW.status != 'baja' THEN
            RAISE EXCEPTION 'Cannot reverse baja status. baja is irreversible (contact_id: %)', OLD.id;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    # 6. Create new trigger with 'baja' name
    op.execute("""
        CREATE TRIGGER enforce_baja_terminal
        BEFORE UPDATE ON contacts
        FOR EACH ROW
        EXECUTE FUNCTION prevent_baja_reversal()
    """)


def downgrade() -> None:
    # Reverse: baja → opt_out
    op.execute("ALTER TABLE contacts DISABLE TRIGGER enforce_baja_terminal")
    op.execute("UPDATE contacts SET status = 'opt_out' WHERE status = 'baja'")
    op.execute("ALTER TABLE contacts ENABLE TRIGGER enforce_baja_terminal")

    op.execute("ALTER TABLE contacts DROP CONSTRAINT contacts_status_check")
    op.execute("""
        ALTER TABLE contacts ADD CONSTRAINT contacts_status_check
        CHECK (status IN ('new', 'contacted', 'hot', 'interview', 'cold', 'opt_out'))
    """)

    op.execute("ALTER TABLE contacts RENAME COLUMN baja_at TO opted_out_at")

    op.execute("DROP TRIGGER IF EXISTS enforce_baja_terminal ON contacts")
    op.execute("DROP FUNCTION IF EXISTS prevent_baja_reversal()")

    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_opt_out_reversal()
        RETURNS TRIGGER AS $$
        BEGIN
          IF OLD.status = 'opt_out' AND NEW.status != 'opt_out' THEN
            RAISE EXCEPTION 'Cannot reverse opt_out status. opt_out is irreversible (contact_id: %%)', OLD.id;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute("""
        CREATE TRIGGER enforce_opt_out_terminal
        BEFORE UPDATE ON contacts
        FOR EACH ROW
        EXECUTE FUNCTION prevent_opt_out_reversal()
    """)

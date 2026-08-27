"""Rename opt_outs table to bajas

Revision ID: 006
Revises: 005
Create Date: 2026-03-03

Changes:
- Table: opt_outs → bajas
- Sequence: opt_outs_id_seq → bajas_id_seq
- PK: opt_outs_pkey → bajas_pkey
- Unique: opt_outs_phone_key → bajas_phone_key
- Index: idx_opt_outs_phone → idx_bajas_phone
- Trigger: sync_opt_out_status → sync_baja_status
- Function: sync_opt_out_to_contacts → sync_baja_to_contacts
  (also fixes body: status='opt_out' → 'baja', opted_out_at → baja_at)
"""
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `scripts/schema.sql` —el baseline— ya crea la tabla con el nombre nuevo,
    # asi que sobre una base recien armada este rename no tiene nada que
    # renombrar y moria con `relation "opt_outs" does not exist`. Mismo guard
    # que la 004 y la 005: la cadena tiene que poder correr sobre el baseline.
    ya_esta = op.get_bind().exec_driver_sql(
        "SELECT to_regclass('public.opt_outs') IS NULL"
    ).scalar()
    if ya_esta:
        return

    # 1. Rename table
    op.execute("ALTER TABLE opt_outs RENAME TO bajas")

    # 2. Rename sequence
    op.execute("ALTER SEQUENCE opt_outs_id_seq RENAME TO bajas_id_seq")

    # 3. Rename constraints and indexes
    op.execute("ALTER INDEX opt_outs_pkey RENAME TO bajas_pkey")
    op.execute("ALTER INDEX opt_outs_phone_key RENAME TO bajas_phone_key")
    op.execute("ALTER INDEX idx_opt_outs_phone RENAME TO idx_bajas_phone")

    # 4. Drop old trigger and function
    op.execute("DROP TRIGGER IF EXISTS sync_opt_out_status ON bajas")
    op.execute("DROP FUNCTION IF EXISTS sync_opt_out_to_contacts()")

    # 5. Create new function with corrected body (baja + baja_at)
    op.execute("""
        CREATE OR REPLACE FUNCTION sync_baja_to_contacts()
        RETURNS TRIGGER AS $$
        BEGIN
          UPDATE contacts
          SET status = 'baja',
              baja_at = NOW()
          WHERE phone = NEW.phone
            AND status != 'baja';
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    # 6. Create new trigger
    op.execute("""
        CREATE TRIGGER sync_baja_status
        AFTER INSERT ON bajas
        FOR EACH ROW
        EXECUTE FUNCTION sync_baja_to_contacts()
    """)


def downgrade() -> None:
    # 1. Drop new trigger and function
    op.execute("DROP TRIGGER IF EXISTS sync_baja_status ON bajas")
    op.execute("DROP FUNCTION IF EXISTS sync_baja_to_contacts()")

    # 2. Rename table back
    op.execute("ALTER TABLE bajas RENAME TO opt_outs")

    # 3. Rename sequence back
    op.execute("ALTER SEQUENCE bajas_id_seq RENAME TO opt_outs_id_seq")

    # 4. Rename constraints and indexes back
    op.execute("ALTER INDEX bajas_pkey RENAME TO opt_outs_pkey")
    op.execute("ALTER INDEX bajas_phone_key RENAME TO opt_outs_phone_key")
    op.execute("ALTER INDEX idx_bajas_phone RENAME TO idx_opt_outs_phone")

    # 5. Recreate old function (with original opt_out references)
    op.execute("""
        CREATE OR REPLACE FUNCTION sync_opt_out_to_contacts()
        RETURNS TRIGGER AS $$
        BEGIN
          UPDATE contacts
          SET status = 'opt_out',
              opted_out_at = NOW()
          WHERE phone = NEW.phone
            AND status != 'opt_out';
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    # 6. Recreate old trigger
    op.execute("""
        CREATE TRIGGER sync_opt_out_status
        AFTER INSERT ON opt_outs
        FOR EACH ROW
        EXECUTE FUNCTION sync_opt_out_to_contacts()
    """)

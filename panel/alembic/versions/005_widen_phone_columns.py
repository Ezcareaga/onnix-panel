"""Widen phone columns from varchar(20) to varchar(50)

Revision ID: 005
Revises: 004
Create Date: 2026-03-03

Fixes: InfoCasas leads with phone numbers > 20 chars cause infinite
error loop in PROC_infocasas_parse → Insert Contact.

Changes:
- contacts.phone: varchar(20) → varchar(50)
- contacts.phone_normalized: varchar(20) → varchar(50)
- opt_outs.phone: varchar(20) → varchar(50)
- users.phone: varchar(20) → varchar(50)
"""
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def _tabla(bind, nombre: str) -> bool:
    return bool(
        bind.exec_driver_sql(
            f"SELECT to_regclass('public.{nombre}') IS NOT NULL"
        ).scalar()
    )


def upgrade() -> None:
    op.execute("ALTER TABLE contacts ALTER COLUMN phone TYPE varchar(50)")
    op.execute("ALTER TABLE contacts ALTER COLUMN phone_normalized TYPE varchar(50)")
    op.execute("ALTER TABLE users ALTER COLUMN phone TYPE varchar(50)")

    # `opt_outs` pasa a llamarse `bajas` en la 006, y `scripts/schema.sql` —el
    # baseline de este repo— ya la crea con el nombre nuevo. Sobre esa base
    # esta linea moria con `relation "opt_outs" does not exist` y frenaba la
    # cadena entera. Se ensancha la que exista; nunca existen las dos.
    bind = op.get_bind()
    tabla = "opt_outs" if _tabla(bind, "opt_outs") else "bajas"
    if _tabla(bind, tabla):
        op.execute(f"ALTER TABLE {tabla} ALTER COLUMN phone TYPE varchar(50)")


def downgrade() -> None:
    op.execute("ALTER TABLE contacts ALTER COLUMN phone TYPE varchar(20)")
    op.execute("ALTER TABLE contacts ALTER COLUMN phone_normalized TYPE varchar(20)")
    op.execute("ALTER TABLE users ALTER COLUMN phone TYPE varchar(20)")

    bind = op.get_bind()
    tabla = "opt_outs" if _tabla(bind, "opt_outs") else "bajas"
    if _tabla(bind, tabla):
        op.execute(f"ALTER TABLE {tabla} ALTER COLUMN phone TYPE varchar(20)")

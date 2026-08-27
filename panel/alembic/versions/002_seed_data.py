"""Seed M2A data: bot_settings + users

Revision ID: 002
Revises: 001
Create Date: 2026-02-23

Requirements covered: MIGA-07 (seed data), MIGA-08
"""
from alembic import op
import bcrypt
import sqlalchemy as sa

# revision identifiers
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ================================================================
    # 1. SEED bot_settings — 10 rows (MIGA-07)
    # ================================================================
    bot_settings_data = [
        ("bot_enabled", "true", "Switch global del bot. false = bot apagado, mensajes de ausencia"),
        ("bot_off_message", "Hola! En este momento nuestro asistente no esta disponible. Podes comunicarte directamente con nosotros al +595986255242. Te responderemos a la brevedad!", "Mensaje cuando bot esta apagado"),
        ("vip_price_threshold_usd", "200000", "Precio USD a partir del cual el lead va directo a la administradora"),
        ("infocasas_poll_interval_min", "5", "Minutos entre cada polling de InfoCasas"),
        ("infocasas_wa_delay_min", "1", "Delay minimo en minutos antes de enviar WhatsApp al lead de InfoCasas"),
        ("infocasas_wa_delay_max", "5", "Delay maximo en minutos antes de enviar WhatsApp al lead de InfoCasas"),
        ("infocasas_reply_delay_min", "60", "Delay minimo en minutos antes de responder en InfoCasas"),
        ("infocasas_reply_delay_max", "300", "Delay maximo en minutos antes de responder en InfoCasas"),
        ("working_hours_start", "08:00", "Hora inicio horario laboral (PYT)"),
        ("working_hours_end", "20:00", "Hora fin horario laboral (PYT)"),
    ]

    for key, value, description in bot_settings_data:
        bind.execute(
            sa.text(
                "INSERT INTO bot_settings (key, value, description) "
                "VALUES (:key, :value, :description)"
            ),
            {"key": key, "value": value, "description": description},
        )

    # ================================================================
    # 2. UPDATE existing admin user (MIGA-08)
    #    DO NOT touch password_hash — we don't know the plaintext
    #    username='admin' and display_name='Administrador' were set in 001
    #    Just ensure phone is set (was not set in 001)
    # ================================================================
    bind.execute(
        sa.text("UPDATE users SET phone = :phone, updated_at = NOW() WHERE id = 1"),
        {"phone": None},
    )

    # ================================================================
    # 3. INSERT new users: Ez (admin) + Operaciones (user) (MIGA-08)
    #
    # `operaciones@` y no `admin@`: el admin id=1 ya lo crea scripts/schema.sql
    # con ese mismo email, y el email es UNIQUE. Sobre una base recien creada
    # con el orden documentado (schema.sql -> alembic upgrade head) esta
    # migracion moria con
    #   duplicate key value violates unique constraint "users_email_key"
    # Es una cuenta de rol, no de persona.
    # ================================================================
    default_password = "OnnixSA2026!"
    password_hash_ez = bcrypt.hashpw(default_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    password_hash_ops = bcrypt.hashpw(default_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    # Ez — admin
    bind.execute(
        sa.text(
            "INSERT INTO users (email, password_hash, name, role, username, display_name, phone, is_active) "
            "VALUES (:email, :password_hash, :name, :role, :username, :display_name, :phone, TRUE)"
        ),
        {
            "email": "ez@onnix.com.py",
            "password_hash": password_hash_ez,
            "name": "Ez",
            "role": "admin",
            "username": "ez",
            "display_name": "Ez",
            "phone": None,
        },
    )

    # Operaciones — user
    bind.execute(
        sa.text(
            "INSERT INTO users (email, password_hash, name, role, username, display_name, phone, is_active) "
            "VALUES (:email, :password_hash, :name, :role, :username, :display_name, :phone, TRUE)"
        ),
        {
            "email": "operaciones@onnix.com.py",
            "password_hash": password_hash_ops,
            "name": "Operaciones",
            "role": "user",
            "username": "operaciones",
            "display_name": "Operaciones",
            "phone": None,
        },
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Remove seeded users (keep original admin id=1)
    bind.execute(sa.text("DELETE FROM users WHERE username IN ('ez', 'operaciones')"))

    # Remove bot_settings data
    bind.execute(sa.text("DELETE FROM bot_settings"))

    # Revert admin phone update (set to NULL as it was)
    bind.execute(sa.text("UPDATE users SET phone = NULL WHERE id = 1"))

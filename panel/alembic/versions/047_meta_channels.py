"""Cambia el CHECK de conversations.channel: sale telegram, entran instagram y messenger

Los tres canales del producto son WhatsApp, Instagram y Messenger. Telegram no
es uno de ellos y se fue del codigo el 2026-09-09; el CHECK que lo permitia lo
fijaba la 003.

`web` y `manual` se quedan: `manual` es el canal de las conversaciones que abre
una persona desde el panel y hoy tiene filas.

Sobre los datos: la migracion mueve a 'manual' cualquier fila que haya quedado
en 'telegram' ANTES de reponer el CHECK, porque una constraint nueva se valida
contra las filas que ya estan y una sola fila sobreviviente aborta el ALTER.
'manual' y no un canal de Meta a proposito — decir que un hilo de Telegram
llego por Instagram seria inventar un dato; 'manual' al menos es cierto en el
sentido que importa: nadie va a poder contestarlo por su canal original.

Revision ID: 047_meta_channels
Revises: 046_lead_events_dead_letter
Create Date: 2026-09-09
"""
from alembic import op

revision = "047_meta_channels"
down_revision = "046_lead_events_dead_letter"
branch_labels = None
depends_on = None

_CANALES_NUEVOS = "('whatsapp', 'web', 'manual', 'instagram', 'messenger')"
_CANALES_VIEJOS = "('whatsapp', 'web', 'manual', 'telegram')"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_channel_check"
    )
    # Sin esto el ADD CONSTRAINT falla contra cualquier fila vieja de Telegram.
    op.execute("UPDATE conversations SET channel = 'manual' WHERE channel = 'telegram'")
    op.execute(
        "ALTER TABLE conversations ADD CONSTRAINT conversations_channel_check "
        f"CHECK (channel IN {_CANALES_NUEVOS})"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_channel_check"
    )
    # Simetrico al upgrade: las filas de Meta no entran en el CHECK viejo.
    # No se puede volver a 'telegram' lo que se movio a 'manual' —esa
    # informacion se perdio a proposito— asi que el downgrade solo garantiza
    # que la constraint vuelva a poder aplicarse.
    op.execute(
        "UPDATE conversations SET channel = 'manual' "
        "WHERE channel IN ('instagram', 'messenger')"
    )
    op.execute(
        "ALTER TABLE conversations ADD CONSTRAINT conversations_channel_check "
        f"CHECK (channel IN {_CANALES_VIEJOS})"
    )

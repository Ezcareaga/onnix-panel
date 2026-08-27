"""lead_events.contact_id pasa a NULLABLE — dead-letter de consultas descartadas.

Revision ID: 046_lead_events_dead_letter
Revises: 045_add_pw_changed_at_to_users
Create Date: 2026-08-24

Por qué
-------
`lead_parser.parse_lead` descarta la consulta que llega sin teléfono ni email y
**no escribe nada**. El dedup del ciclo siguiente
(`notification_fetcher.check_existing_ids`) busca el `consulta_id` en
`contacts.source_id` y en `lead_events.metadata->>'consulta_id'`; como no está
en ninguno de los dos, la vuelve a levantar. Medido el 2026-08-24: los mismos
6 `consulta_id` en cada corrida del poll, cada 5 minutos — 12 % de las 50
notificaciones y ~1.728 llamadas GraphQL inútiles por día.

El rastro tiene que ser un `lead_event`, porque es la tabla que el dedup ya
mira. Pero la consulta descartada **no tiene contacto**: por definición no se
pudo crear uno. `contact_id` era NOT NULL en la base aunque el modelo
(`app/models/lead_event.py`) ya lo declaraba `Optional[int]`. Esta migración
pone la base de acuerdo con el modelo.

Qué NO rompe
------------
Todas las lecturas de `lead_events` filtran por un `contact_id` concreto
(`LeadEventRepository.get_by_contact` / `get_detail_views` / `get_all_events`,
el timeline del contacto, `followup_sender`). Una fila con `contact_id NULL`
es invisible para todas ellas — que es exactamente lo que se quiere: el
dead-letter existe para el dedup y para la evidencia, no para la ficha.

Downgrade
---------
Borra las filas huérfanas antes de restaurar el NOT NULL. Son dead-letters,
no datos de negocio; sin eso el `ALTER` falla y la 046 no se puede revertir.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "046_lead_events_dead_letter"
down_revision = "045_add_pw_changed_at_to_users"
branch_labels = None
depends_on = None

TABLE = "lead_events"
COLUMN = "contact_id"


def upgrade() -> None:
    op.alter_column(TABLE, COLUMN, existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute(f"DELETE FROM {TABLE} WHERE {COLUMN} IS NULL")
    op.alter_column(TABLE, COLUMN, existing_type=sa.Integer(), nullable=False)

"""Migración 046 — lead_events.contact_id pasa a NULLABLE (dead-letter).

Corre contra la scratch DB de esta carpeta (`_migration_scratch_db`), nunca
contra onnix_dev ni contra producción.

Lo que fija:
  - Después de aplicar la 046, `lead_events.contact_id` acepta NULL y el
    INSERT del dead-letter entra.
  - El dedup lo encuentra por `metadata->>'consulta_id'` — que es el punto
    entero del rastro.
  - Después del downgrade vuelve el NOT NULL y ese mismo INSERT es
    rechazado: la prueba negativa, sin la cual "es nullable" no prueba nada.
  - El downgrade limpia las filas huérfanas. Sin ese DELETE el ALTER falla y
    la 046 no se puede revertir.

TRAMPA DEL HARNESS, encontrada acá el 2026-08-24 y contenida en este archivo
-----------------------------------------------------------------------------
`_migration_scratch_db` (conftest.py de esta carpeta) arma la scratch DB con un
`pg_dump --schema-only` de onnix_dev —o sea, con el esquema REAL, hoy 045— y
después le escribe a mano ``INSERT INTO alembic_version VALUES ('040_visits')``.
Su docstring dice que la lleva a HEAD con `alembic upgrade head`; no es lo que
hace. El literal '040' era cierto cuando dev estaba en 040 y quedó viejo.

Consecuencia: la base DICE 040 y TIENE 045. Ningún test lo notó porque los 22
que ya existían sólo se mueven entre 038/039/040. El primero que intenta subir
por encima de 041 —éste— replaya la 043 sobre un índice que ya existe y muere
con `relation "idx_contacts_status_created" already exists`.

Arreglarlo en el conftest sería stampear la revisión que dev reporta de verdad,
pero eso rompe a los otros 22: sus `ensure_head_038/039/040` abortan con
"Unexpected alembic head" ante cualquier valor que no sea 038/039/040. Es una
tanda aparte y queda anotada. Acá se corrige la marca sólo para estos tests
—decir 045 sobre un esquema 045 es decir la verdad, no falsearla— y se la deja
como estaba al salir, para que el autouse compartido siga viendo lo que espera.
"""
from __future__ import annotations

import pytest

from .conftest import alembic_cmd, current_alembic_head, psql

HEAD_046 = "046_lead_events_dead_letter"
HEAD_045 = "045_add_pw_changed_at_to_users"
HEAD_040 = "040_visits"

_INSERT_HUERFANO = (
    "INSERT INTO lead_events (contact_id, event_type, triggered_by, metadata) "
    "VALUES (NULL, 'discarded_no_contact', 'infocasas_poll', "
    "'{\"consulta_id\": \"69577799\"}'::jsonb);"
)


def _stamp(version: str) -> None:
    res = psql(f"UPDATE alembic_version SET version_num = '{version}';")
    assert res.returncode == 0, f"no se pudo stampear {version}: {res.stderr}"


@pytest.fixture(autouse=True)
def scratch_en_045():
    """Deja la scratch DB en el estado pre-046 de verdad, y no por casualidad.

    La version anterior **asumia** que el esquema copiado estaba pre-046 y
    stampeaba 045 encima. Eso funciono exactamente un dia: en cuanto la 046 se
    aplico a `onnix_dev`, el `pg_dump --schema-only` empezo a traer la
    columna ya nullable, y `test_antes_de_la_046_el_not_null_esta` quedo
    **rojo para siempre** — no por el codigo, sino porque su premisa dejo de
    ser reproducible.

    Es la misma leccion que el resto del archivo ya documenta, un escalon mas
    arriba: un test de migracion no puede depender de en que revision quedo la
    base de la que se copio el esquema. **La precondicion se construye.**

    Se borran las huerfanas antes del ALTER porque `SET NOT NULL` falla si hay
    una sola fila con NULL — y este mismo archivo prueba mas abajo que el
    downgrade tiene que limpiarlas por el mismo motivo.
    """
    psql("DELETE FROM lead_events WHERE contact_id IS NULL;")
    res = psql("ALTER TABLE lead_events ALTER COLUMN contact_id SET NOT NULL;")
    assert res.returncode == 0, (
        f"no se pudo llevar la scratch DB al estado pre-046: {res.stderr}"
    )
    _stamp(HEAD_045)
    yield
    if current_alembic_head() == HEAD_046:
        res = alembic_cmd("downgrade", HEAD_045)
        assert res.returncode == 0, (
            f"no se pudo revertir la 046 al terminar:\n{res.stdout}\n{res.stderr}"
        )
    _stamp(HEAD_040)


def _upgrade_a_046() -> None:
    if current_alembic_head() == HEAD_046:
        return
    res = alembic_cmd("upgrade", HEAD_046)
    assert res.returncode == 0, f"upgrade a 046 falló:\n{res.stdout}\n{res.stderr}"


def _nullability() -> str:
    res = psql(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'lead_events' AND column_name = 'contact_id';"
    )
    assert res.returncode == 0, f"psql falló: {res.stderr}"
    return res.stdout.strip()


class TestUpgrade046:
    def test_antes_de_la_046_el_not_null_esta(self):
        """La premisa. Si esto ya fuera 'YES', los tests de abajo no probarían
        que la migración hace algo."""
        assert current_alembic_head() == HEAD_045
        assert _nullability() == "NO"

    def test_contact_id_queda_nullable(self):
        _upgrade_a_046()
        assert _nullability() == "YES", (
            "post-046 lead_events.contact_id tiene que aceptar NULL — sin eso "
            "el dead-letter no se puede escribir"
        )

    def test_insert_huerfano_entra(self):
        _upgrade_a_046()
        psql("DELETE FROM lead_events WHERE contact_id IS NULL;")
        res = psql(_INSERT_HUERFANO)
        assert res.returncode == 0, (
            f"el INSERT del dead-letter fue rechazado post-046: {res.stderr}"
        )

    def test_el_dedup_lo_encuentra_por_metadata(self):
        """La query real de check_existing_ids, contra la fila huérfana. Es el
        punto entero del dead-letter: si el dedup no la ve, el poll vuelve a
        pedir el mismo consulta_id cada 5 minutos."""
        _upgrade_a_046()
        psql("DELETE FROM lead_events WHERE contact_id IS NULL;")
        assert psql(_INSERT_HUERFANO).returncode == 0
        res = psql(
            "SELECT metadata->>'consulta_id' FROM lead_events "
            "WHERE metadata->>'consulta_id' = ANY(ARRAY['69577799']);"
        )
        assert res.stdout.strip() == "69577799", (
            "el dedup no encuentra el dead-letter: el poll lo va a reprocesar"
        )


class TestDowngrade046:
    def test_roundtrip_restaura_el_not_null_y_limpia_huerfanos(self):
        _upgrade_a_046()
        psql("DELETE FROM lead_events WHERE contact_id IS NULL;")
        assert psql(_INSERT_HUERFANO).returncode == 0

        down = alembic_cmd("downgrade", HEAD_045)
        assert down.returncode == 0, (
            f"downgrade a 045 falló — el DELETE de huérfanos no alcanzó:\n"
            f"{down.stdout}\n{down.stderr}"
        )
        assert current_alembic_head() == HEAD_045
        assert _nullability() == "NO", "post-downgrade el NOT NULL tiene que volver"

        # Prueba negativa: el mismo INSERT que entraba ahora tiene que fallar.
        rechazado = psql(_INSERT_HUERFANO)
        assert rechazado.returncode != 0, (
            "post-downgrade el INSERT con contact_id NULL seguía entrando: "
            "el NOT NULL no volvió de verdad"
        )

        up = alembic_cmd("upgrade", HEAD_046)
        assert up.returncode == 0, f"re-upgrade falló:\n{up.stdout}\n{up.stderr}"
        assert _nullability() == "YES"

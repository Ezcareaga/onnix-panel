#!/usr/bin/env bash
# Crea (o recrea) una base de test vacía y usable por la suite de pytest.
#
#   scripts/make_test_db.sh onnix_test_gw_0
#
# La llama tests/conftest.py una vez por worker de xdist. Tarda ~6 s.
#
# ESTRUCTURA: pg_dump --schema-only de onnix_dev. NO se usa
# `scripts/schema.sql` + `alembic upgrade head`, aunque el encabezado de
# schema.sql diga que ése es el camino: schema.sql ya trae adentro el estado
# POST-004 (columna `baja_at`, trigger `enforce_baja_terminal`), así que la
# migración 004 muere sobre esa base con
#   trigger "enforce_opt_out_terminal" for table "contacts" does not exist
# Es la misma técnica que panel/tests/migrations/conftest.py ya usa para su
# scratch DB, y funciona.
#
# DATOS: scripts/seed_test.sql — lo mínimo compartido que la suite asume.
set -euo pipefail

DB=${1:?uso: make_test_db.sh <nombre_de_base>}
case "$DB" in
    onnix_test_*) ;;
    *) echo "make_test_db.sh: '$DB' no es un nombre de base de test — abortado" >&2; exit 2;;
esac

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PGC=${PG_CONTAINER:-onnix-postgres}
PGU=${PG_USER:-onnix}
SRC=${SOURCE_DB:-onnix_dev}
PY=${PYTHON:-python3}

adm() { docker exec -i "$PGC" psql -U "$PGU" -d postgres -v ON_ERROR_STOP=1 -q "$@"; }
on()  { docker exec -i "$PGC" psql -U "$PGU" -d "$DB"   -v ON_ERROR_STOP=1 -q "$@"; }

adm -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB' AND pid <> pg_backend_pid()" >/dev/null
adm -c "DROP DATABASE IF EXISTS $DB"
adm -c "CREATE DATABASE $DB TEMPLATE template0"

# Las extensiones no salen en un dump `-n public`, y sin ellas el schema no
# entra: properties.embedding necesita `vector` y los índices GIN necesitan
# pg_trgm.
on -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"; CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS unaccent; CREATE EXTENSION IF NOT EXISTS vector;'

# El `CREATE SCHEMA public` del dump choca con el que ya trae template0 y sería
# el único error del camino: se filtra para poder cargar el resto con
# ON_ERROR_STOP y que cualquier OTRO error sí mate la corrida.
docker exec "$PGC" bash -c \
    "pg_dump -U $PGU --schema-only --no-owner --no-privileges -n public $SRC" \
  | grep -v '^CREATE SCHEMA public;$' \
  | docker exec -i "$PGC" psql -U "$PGU" -d "$DB" -v ON_ERROR_STOP=1 -q -o /dev/null

# El dump trae `alembic_version` vacía. La marca la pone alembic, no un INSERT
# a mano: así la base dice la misma revisión que el árbol de migraciones.
( cd "$ROOT/panel" && POSTGRES_HOST=127.0.0.1 POSTGRES_DB="$DB" "$PY" -m alembic stamp head >/dev/null )

on -v admin_password="${TEST_ADMIN_PASSWORD:?TEST_ADMIN_PASSWORD no está seteada (ver CLAUDE.md, trampas conocidas)}" \
   -f - < "$ROOT/scripts/seed_test.sql"

# Chequeo barato: un dump que entró a medias deja una base que parece sana
# hasta que un test falla por una tabla que no está.
tablas=$(docker exec "$PGC" psql -U "$PGU" -d "$DB" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
if [ "$tablas" -lt 15 ]; then
    echo "make_test_db.sh: $DB quedó con $tablas tablas, se esperaban 15 o más" >&2
    exit 1
fi

echo "base de test lista: $DB ($tablas tablas)"

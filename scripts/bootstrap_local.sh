#!/usr/bin/env bash
# Levanta el proyecto en la laptop, de cero, sin tocar nada del VPS.
#
#   scripts/bootstrap_local.sh          # arma la base si no existe y levanta
#   scripts/bootstrap_local.sh --reset  # BORRA la base local y la rehace
#
# El orden importa y está documentado en el encabezado de scripts/schema.sql:
# Alembic NO puede crear la base desde cero —la cadena arranca en 001, que ya
# asume las tablas base—, así que va primero el baseline y después la cadena.
set -euo pipefail

RAIZ=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$RAIZ"

COMPOSE=(docker compose -f docker-compose.local.yml --env-file .env.local)
PGC=onnix-postgres
PGU=onnix
DB=onnix_dev

if [[ ! -f .env.local ]]; then
    echo "falta .env.local — copiar de .env.example y ajustar" >&2
    exit 1
fi

echo "==> postgres"
"${COMPOSE[@]}" up -d postgres
until docker exec "$PGC" pg_isready -U "$PGU" -d postgres >/dev/null 2>&1; do sleep 1; done

if [[ "${1:-}" == "--reset" ]]; then
    echo "==> borrando $DB"
    docker exec -i "$PGC" psql -U "$PGU" -d postgres -q \
        -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)"
fi

if ! docker exec -i "$PGC" psql -U "$PGU" -d postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname='$DB'" | grep -q 1; then
    echo "==> creando $DB"
    docker exec -i "$PGC" psql -U "$PGU" -d postgres -q -c "CREATE DATABASE $DB"
fi

# vector va aparte: sin la extensión, properties.description_embedding no se
# puede crear y schema.sql muere en la primera tabla grande.
echo "==> extensiones"
docker exec -i "$PGC" psql -U "$PGU" -d "$DB" -q -v ON_ERROR_STOP=1 \
    -c "CREATE EXTENSION IF NOT EXISTS vector" \
    -c "CREATE EXTENSION IF NOT EXISTS unaccent" \
    -c "CREATE EXTENSION IF NOT EXISTS pg_trgm" \
    -c "CREATE EXTENSION IF NOT EXISTS pgcrypto"

echo "==> baseline (scripts/schema.sql)"
docker exec -i "$PGC" psql -U "$PGU" -d "$DB" -q -v ON_ERROR_STOP=1 < scripts/schema.sql

# Las migraciones las corre el entrypoint del contenedor (`alembic upgrade
# head`). Si falla, el panel no arranca y el log lo dice.
echo "==> panel (build + up)"
"${COMPOSE[@]}" up -d --build panel

echo "==> esperando el health"
for _ in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8010/health >/dev/null 2>&1; then
        echo
        echo "Panel arriba: http://127.0.0.1:8010"
        echo "  admin@onnix.com.py / OnnixAdmin2026!   (lo siembra schema.sql)"
        echo "  ez@onnix.com.py    / OnnixSA2026!      (lo siembra la migración 002)"
        echo
        echo "Cambiá las dos contraseñas antes de exponer esto a cualquier red."
        exit 0
    fi
    sleep 2
done

echo "el panel no respondió el health en 120s — 'docker logs onnix-panel'" >&2
exit 1

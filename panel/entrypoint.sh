#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] Running 'alembic upgrade head'..."
alembic upgrade head
echo "[entrypoint] Migrations up-to-date. Starting uvicorn."

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --proxy-headers \
    --forwarded-allow-ips='*'

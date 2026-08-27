#!/usr/bin/env bash
# M6.4b — Aplica rate-limit nginx para /propiedades (runbook: docs/ops/NGINX_PROPIEDADES_RATELIMIT.md)
# Correr con: sudo bash scripts/apply_nginx_propiedades_ratelimit.sh
# Idempotente: si ya está aplicado, no duplica nada.
set -euo pipefail

NGINX_CONF=/etc/nginx/nginx.conf
SITE_CONF=/etc/nginx/sites-enabled/onnix   # archivo regular ACTIVO (sites-available está stale)
BACKUP_DIR=/etc/nginx/backups                  # NUNCA dentro de sites-enabled/ — nginx incluye todo ese dir
STAMP=$(date +%Y%m%d-%H%M%S)

[ "$(id -u)" -eq 0 ] || { echo "ERROR: correr con sudo"; exit 1; }
mkdir -p "$BACKUP_DIR"

# --- 1. Zona en nginx.conf (junto a zone=login) ---
if grep -q "zone=propiedades" "$NGINX_CONF"; then
    echo "[skip] zona 'propiedades' ya existe en nginx.conf"
else
    cp "$NGINX_CONF" "$BACKUP_DIR/nginx.conf.bak-$STAMP"
    sed -i '/zone=login:10m rate=5r\/m;/a\	limit_req_zone $binary_remote_addr zone=propiedades:10m rate=2r/s;' "$NGINX_CONF"
    grep -q "zone=propiedades" "$NGINX_CONF" || { echo "ERROR: no se insertó la zona"; exit 1; }
    echo "[ok] zona 'propiedades' agregada (backup: $BACKUP_DIR/nginx.conf.bak-$STAMP)"
fi

# --- 2. Location /propiedades en el server 443, antes del catch-all location / ---
if grep -q "location /propiedades" "$SITE_CONF"; then
    echo "[skip] location /propiedades ya existe"
else
    cp "$SITE_CONF" "$BACKUP_DIR/onnix.bak-$STAMP"
    # Inserta antes de la línea "# Panel Admin (FastAPI)" (anchor verificado, línea ~71)
    sed -i '/# Panel Admin (FastAPI)/i\
    # Portal publico M6.4b — rate-limit anti-scraper (2r/s + burst 10 por IP)\
    location /propiedades {\
        limit_req zone=propiedades burst=10 nodelay;\
        limit_req_status 429;\
        proxy_pass http://127.0.0.1:8000;\
        proxy_set_header Host $host;\
        proxy_set_header X-Real-IP $remote_addr;\
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\
        proxy_set_header X-Forwarded-Proto $scheme;\
    }\
' "$SITE_CONF"
    grep -q "location /propiedades" "$SITE_CONF" || { echo "ERROR: no se insertó el location"; exit 1; }
    echo "[ok] location /propiedades agregado (backup: $BACKUP_DIR/onnix.bak-$STAMP)"
fi

# --- 3. Validar y recargar ---
nginx -t
systemctl reload nginx
echo "[ok] nginx recargado"

# --- 4. Verificar ---
code=$(curl -s -o /dev/null -w "%{http_code}" -H "Host: onnix.com.py" https://127.0.0.1/propiedades --insecure)
echo "GET /propiedades (local via nginx): $code (esperado 200)"
echo "Burst test (35 requests rápidos — los últimos deben dar 429):"
for i in $(seq 1 35); do
    curl -s -o /dev/null -w "%{http_code} " -H "Host: onnix.com.py" https://127.0.0.1/propiedades --insecure
done
echo
echo "[done] Rate-limit /propiedades aplicado. /login no tocado."

#!/usr/bin/env bash
# fix/hardening-mi-cuenta-pre-m7 — Aplica C1 (real IP Cloudflare) + C2 (rate-limit /login)
# Runbook completo: docs/ops/NGINX_LOGIN_HARDENING.md
# Patch de referencia: scripts/nginx_login_hardening_patch.conf
#
# Correr con: sudo bash scripts/apply_nginx_login_hardening.sh
# Idempotente: si cada cambio ya está aplicado, lo salta con [skip].
set -euo pipefail

NGINX_CONF=/etc/nginx/nginx.conf
SITE_CONF=/etc/nginx/sites-enabled/onnix   # archivo regular ACTIVO (sites-available está stale)
BACKUP_DIR=/etc/nginx/backups                  # NUNCA dentro de sites-enabled/ — nginx incluye todo ese dir
STAMP=$(date +%Y%m%d-%H%M%S)

[ "$(id -u)" -eq 0 ] || { echo "ERROR: correr con sudo"; exit 1; }
mkdir -p "$BACKUP_DIR"

# =============================================================================
# PASO 1 — Zona login_limit en /etc/nginx/nginx.conf (http{} block)
# La zona va en http{}, NO en server{}. El apply script añade la línea
# después de la zona 'propiedades' existente (línea ~60).
# =============================================================================
if grep -q "zone=login_limit" "$NGINX_CONF"; then
    echo "[skip] zona 'login_limit' ya existe en nginx.conf"
else
    cp "$NGINX_CONF" "$BACKUP_DIR/nginx.conf.bak-$STAMP"
    # Inserta después de la zona propiedades (anchor verificado)
    sed -i '/zone=propiedades:10m rate=2r\/s;/a\	limit_req_zone $binary_remote_addr zone=login_limit:10m rate=10r/m;\n\tlimit_req_status 429;' "$NGINX_CONF"
    grep -q "zone=login_limit" "$NGINX_CONF" || { echo "ERROR: no se insertó zona login_limit"; exit 1; }
    echo "[ok] zona 'login_limit' agregada en nginx.conf (backup: $BACKUP_DIR/nginx.conf.bak-$STAMP)"
fi

# =============================================================================
# PASO 2 — Bloque set_real_ip_from (C1) en /etc/nginx/sites-enabled/onnix
# Va dentro del server{} 443, ANTES del primer location block.
# Anchor: la línea "ssl_protocols TLSv1.2 TLSv1.3;" (siempre presente).
# =============================================================================
if grep -q "set_real_ip_from 173.245.48.0" "$SITE_CONF"; then
    echo "[skip] bloque set_real_ip_from (C1) ya existe en $SITE_CONF"
else
    cp "$SITE_CONF" "$BACKUP_DIR/onnix.bak-$STAMP"
    # Rango de IPs obtenido el 2026-06-13 desde https://www.cloudflare.com/ips-v4/ e /ips-v6/
    sed -i '/ssl_protocols TLSv1\.2 TLSv1\.3;/a\
\
    # --- C1: Cloudflare Real IP -----------------------------------------------\
    # Rangos obtenidos el 2026-06-13 desde https://www.cloudflare.com/ips-v4/ y /ips-v6/\
    # Renovar periodicamente (lista canonica publicada por Cloudflare).\
    # Tras este bloque, $remote_addr = IP REAL del visitante (no el edge CF).\
    # IPv4\
    set_real_ip_from 173.245.48.0/20;\
    set_real_ip_from 103.21.244.0/22;\
    set_real_ip_from 103.22.200.0/22;\
    set_real_ip_from 103.31.4.0/22;\
    set_real_ip_from 141.101.64.0/18;\
    set_real_ip_from 108.162.192.0/18;\
    set_real_ip_from 190.93.240.0/20;\
    set_real_ip_from 188.114.96.0/20;\
    set_real_ip_from 197.234.240.0/22;\
    set_real_ip_from 198.41.128.0/17;\
    set_real_ip_from 162.158.0.0/15;\
    set_real_ip_from 104.16.0.0/13;\
    set_real_ip_from 104.24.0.0/14;\
    set_real_ip_from 172.64.0.0/13;\
    set_real_ip_from 131.0.72.0/22;\
    # IPv6\
    set_real_ip_from 2400:cb00::/32;\
    set_real_ip_from 2606:4700::/32;\
    set_real_ip_from 2803:f800::/32;\
    set_real_ip_from 2405:b500::/32;\
    set_real_ip_from 2405:8100::/32;\
    set_real_ip_from 2a06:98c0::/29;\
    set_real_ip_from 2c0f:f248::/32;\
    real_ip_header    CF-Connecting-IP;\
    real_ip_recursive on;\
    # --------------------------------------------------------------------------' "$SITE_CONF"
    grep -q "set_real_ip_from 173.245.48.0" "$SITE_CONF" || { echo "ERROR: no se insertó bloque real_ip"; exit 1; }
    echo "[ok] bloque set_real_ip_from (C1) agregado (backup: $BACKUP_DIR/onnix.bak-$STAMP)"
fi

# =============================================================================
# PASO 3 — Location = /login (C2) en /etc/nginx/sites-enabled/onnix
# Va ANTES del bloque "# Panel Admin (FastAPI)" (catch-all location /).
# Anchor: "# Panel Admin (FastAPI)" (verificado en config actual, línea ~71).
# =============================================================================
if grep -q "location = /login" "$SITE_CONF"; then
    echo "[skip] location = /login ya existe en $SITE_CONF"
else
    # Backup ya tomado en paso 2 (o tomarlo si el paso 2 fue skipped)
    [ -f "$BACKUP_DIR/onnix.bak-$STAMP" ] || cp "$SITE_CONF" "$BACKUP_DIR/onnix.bak-$STAMP"
    sed -i '/# Panel Admin (FastAPI)/i\
    # --- C2: Login rate-limit -------------------------------------------------\
    # 10 req\/min por IP real (post-C1). burst=5 nodelay -> 16a req -> 429.\
    # Dependencia: set_real_ip_from (C1) resuelve $remote_addr primero.\
    location = \/login {\
        limit_req zone=login_limit burst=5 nodelay;\
        limit_req_status 429;\
        proxy_pass http:\/\/127.0.0.1:8000;\
        proxy_set_header Host                $host;\
        proxy_set_header X-Real-IP           $remote_addr;\
        proxy_set_header X-Forwarded-For     $proxy_add_x_forwarded_for;\
        proxy_set_header X-Forwarded-Proto   $scheme;\
        proxy_set_header CF-Connecting-IP    $http_cf_connecting_ip;\
        proxy_buffering off;\
        proxy_connect_timeout 60s;\
        proxy_send_timeout    60s;\
        proxy_read_timeout    60s;\
    }\
    # --------------------------------------------------------------------------\
' "$SITE_CONF"
    grep -q "location = /login" "$SITE_CONF" || { echo "ERROR: no se insertó location = /login"; exit 1; }
    echo "[ok] location = /login (C2) agregado (backup: $BACKUP_DIR/onnix.bak-$STAMP)"
fi

# =============================================================================
# PASO 4 — CF-Connecting-IP header en los location blocks existentes
# Agrega proxy_set_header CF-Connecting-IP $http_cf_connecting_ip;
# en location / (catch-all), /conversations/sse, /webhook/telegram, /webhook/whatsapp.
# Idempotente: salta si ya existe.
# =============================================================================
if grep -q "CF-Connecting-IP" "$SITE_CONF"; then
    echo "[skip] CF-Connecting-IP ya presente en $SITE_CONF"
else
    [ -f "$BACKUP_DIR/onnix.bak-$STAMP" ] || cp "$SITE_CONF" "$BACKUP_DIR/onnix.bak-$STAMP"
    # Agrega CF-Connecting-IP después de cada línea X-Forwarded-Proto en location blocks de FastAPI
    sed -i 's/proxy_set_header X-Forwarded-Proto   \$scheme;$/proxy_set_header X-Forwarded-Proto   $scheme;\n        proxy_set_header CF-Connecting-IP    $http_cf_connecting_ip;/' "$SITE_CONF"
    grep -q "CF-Connecting-IP" "$SITE_CONF" || { echo "ERROR: no se insertó CF-Connecting-IP"; exit 1; }
    echo "[ok] CF-Connecting-IP agregado en location blocks FastAPI"
fi

# =============================================================================
# PASO 5 — Validar y recargar
# =============================================================================
nginx -t
systemctl reload nginx
echo "[ok] nginx recargado"

# =============================================================================
# PASO 6 — Verificación básica
# =============================================================================
echo ""
echo "=== Verificación básica ==="
code=$(curl -s -o /dev/null -w "%{http_code}" -H "Host: onnix.com.py" https://127.0.0.1/login --insecure)
echo "GET /login (local via nginx): $code (esperado 200)"

echo "Burst test (16 requests rápidos — el req 16 debe dar 429):"
for i in $(seq 1 16); do
    curl -s -o /dev/null -w "%{http_code} " -H "Host: onnix.com.py" https://127.0.0.1/login --insecure
done
echo ""
echo "[done] C1+C2 aplicados. Ver runbook completo: docs/ops/NGINX_LOGIN_HARDENING.md"
echo "RECORDATORIO: Verificar que TRUST_PROXY_HEADERS=true está en .env (prod y staging)."

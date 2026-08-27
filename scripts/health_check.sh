#!/bin/bash
# Health check rápido — corre cada 15 min
# v3: N8N checks removed (migrated to FastAPI), embeddings check added, alert dedup
PROJECT_DIR="${ONNIX_STATE_DIR:-/home/onnix}"  # estado del servidor: .env, logs/, backups/
LOG_SCRIPT="$(dirname "${BASH_SOURCE[0]}")/log.sh"
DEDUP_FILE="/tmp/health_check_last_alert.json"
DEDUP_WINDOW=3600  # 1 hora entre alertas del mismo tipo
ISSUES=""

# Load env (for TELEGRAM_BOT_TOKEN, TELEGRAM_EZ_CHAT_ID)
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi

# ------------------------------------------------------------------
# Dedup helper: returns 0 (true) if alert should be sent
# ------------------------------------------------------------------
should_alert() {
    local key="$1"
    local now
    now=$(date +%s)

    if [ ! -f "$DEDUP_FILE" ]; then
        echo "{}" > "$DEDUP_FILE"
    fi

    local last
    last=$(python3 -c "
import json, sys
try:
    with open('$DEDUP_FILE') as f:
        data = json.load(f)
    print(data.get('$key', 0))
except Exception:
    print(0)
" 2>/dev/null)

    if [ $(( now - ${last:-0} )) -ge $DEDUP_WINDOW ]; then
        # Update timestamp
        python3 -c "
import json
try:
    with open('$DEDUP_FILE') as f:
        data = json.load(f)
except Exception:
    data = {}
data['$key'] = $now
with open('$DEDUP_FILE', 'w') as f:
    json.dump(data, f)
" 2>/dev/null
        return 0  # should alert
    fi
    return 1  # skip (already alerted recently)
}

# ------------------------------------------------------------------
# Checks
# ------------------------------------------------------------------

# 1. Docker containers (solo panel + postgres)
PG_STATUS=$(docker inspect -f '{{.State.Running}}' onnix-postgres 2>/dev/null)
PANEL_STATUS=$(docker inspect -f '{{.State.Running}}' onnix-panel 2>/dev/null)

if [ "$PG_STATUS" != "true" ] && should_alert "postgres_down"; then
    ISSUES="${ISSUES}🔴 onnix-postgres DOWN\n"
fi
if [ "$PANEL_STATUS" != "true" ] && should_alert "panel_down"; then
    ISSUES="${ISSUES}🔴 onnix-panel DOWN\n"
fi

# 2. Disk space /
DISK_USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DISK_USAGE" -gt 85 ] && should_alert "disk_full"; then
    ISSUES="${ISSUES}⚠️ Disco / al ${DISK_USAGE}%\n"
fi

# 2b. Disk space /backups (solo si partición separada)
ROOT_DEV=$(df / 2>/dev/null | tail -1 | awk '{print $1}')
BACKUP_DEV=$(df "$PROJECT_DIR/backups" 2>/dev/null | tail -1 | awk '{print $1}')
BACKUP_DISK=$(df "$PROJECT_DIR/backups" 2>/dev/null | tail -1 | awk '{print $5}' | tr -d '%')
if [ -n "$BACKUP_DISK" ] && [ "$BACKUP_DEV" != "$ROOT_DEV" ] && [ "$BACKUP_DISK" -gt 85 ] 2>/dev/null && should_alert "backup_disk_full"; then
    ISSUES="${ISSUES}⚠️ Disco /backups al ${BACKUP_DISK}%\n"
fi

# 3. RAM > 80%
RAM_USAGE=$(free | awk '/Mem:/ {printf "%.0f", $3/$2 * 100}')
if [ "$RAM_USAGE" -gt 80 ] && should_alert "ram_high"; then
    ISSUES="${ISSUES}⚠️ RAM al ${RAM_USAGE}%\n"
fi

# 4. Panel admin responde (HTTP health check)
if [ "$PANEL_STATUS" = "true" ]; then
    PANEL_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:8000 2>/dev/null)
    if [ "$PANEL_HTTP" != "200" ] && [ "$PANEL_HTTP" != "303" ] && should_alert "panel_http_fail"; then
        ISSUES="${ISSUES}🔴 Panel admin no responde (HTTP ${PANEL_HTTP:-timeout})\n"
    fi
fi

# 5. DB responde (SELECT 1)
if [ "$PG_STATUS" = "true" ]; then
    DB_CHECK=$(docker exec onnix-postgres psql -U onnix -d onnix_prod -t -c "SELECT 1;" 2>/dev/null | tr -d ' \n')
    if [ "$DB_CHECK" != "1" ] && should_alert "db_down"; then
        ISSUES="${ISSUES}🔴 DB no responde (SELECT 1 failed)\n"
    fi
fi

# 6. Bot errors últimos 30 min
ERROR_COUNT=$(docker exec onnix-postgres psql -U onnix -d onnix_prod -t -c \
    "SELECT COUNT(*) FROM bot_errors WHERE created_at > NOW() - INTERVAL '30 minutes';" 2>/dev/null | tr -d ' \n')

if [ -n "$ERROR_COUNT" ] && [ "$ERROR_COUNT" -gt 0 ] 2>/dev/null && should_alert "bot_errors"; then
    ISSUES="${ISSUES}⚠️ ${ERROR_COUNT} errores en últimos 30 min\n"
fi

# 7. Embeddings pendientes (propiedades activas >24h sin embedding, con descripción >200 chars)
if [ "$PG_STATUS" = "true" ]; then
    MISSING_EMBEDDINGS=$(docker exec onnix-postgres psql -U onnix -d onnix_prod -t -c \
        "SELECT COUNT(*) FROM properties WHERE is_active = true AND description_embedding IS NULL AND created_at < NOW() - INTERVAL '24 hours' AND LENGTH(COALESCE(description, '')) > 200;" 2>/dev/null | tr -d ' \n')

    if [ -n "$MISSING_EMBEDDINGS" ] && [ "$MISSING_EMBEDDINGS" -gt 100 ] 2>/dev/null && should_alert "embeddings_missing"; then
        ISSUES="${ISSUES}⚠️ ${MISSING_EMBEDDINGS} propiedades sin embedding (creadas hace >24h)\n"
    fi
fi

# ------------------------------------------------------------------
# Reportar
# ------------------------------------------------------------------
if [ -n "$ISSUES" ]; then
    $LOG_SCRIPT WARN SYSTEM "Health check issues found" "{\"issues\": \"$(echo -e $ISSUES | tr '\n' ' ')\"}"
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TELEGRAM_EZ_CHAT_ID}" \
        --data-urlencode "text=⚠️ HEALTH CHECK ALERT
${ISSUES}" > /dev/null
else
    $LOG_SCRIPT INFO SYSTEM "Health check OK" "{\"disk\": \"${DISK_USAGE}%\", \"ram\": \"${RAM_USAGE}%\"}"
fi

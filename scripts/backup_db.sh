#!/usr/bin/env bash
# =============================================================================
# Onnix SA — PostgreSQL Daily Backup Script
# Runs via cron at 01:00 daily. Uses pg_dump custom format with compression.
# Rotation: keeps last 7 days of backups.
# =============================================================================

set -euo pipefail

# ---------------------------
# Configuration
# ---------------------------
PROJECT_DIR="${ONNIX_STATE_DIR:-/home/onnix}"  # estado del servidor: .env, logs/, backups/
BACKUP_DIR="${PROJECT_DIR}/backups"
LOG_FILE="${PROJECT_DIR}/logs/system/backup.log"
RETENTION_DAYS=7
CONTAINER_NAME="onnix-postgres"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/onnix_prod_${TIMESTAMP}.dump"

# ---------------------------
# Load environment variables
# ---------------------------
if [ -f "${PROJECT_DIR}/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "${PROJECT_DIR}/.env"
    set +a
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] [backup] .env file not found at ${PROJECT_DIR}/.env" >> "$LOG_FILE"
    exit 1
fi

# ---------------------------
# Helper: log function
# ---------------------------
log() {
    local level="$1"
    local message="$2"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] [backup] ${message}" >> "$LOG_FILE"
}

# ---------------------------
# Pre-flight checks
# ---------------------------

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# Check if the container is running
if ! docker inspect --format='{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null | grep -q 'true'; then
    log "ERROR" "Container ${CONTAINER_NAME} is not running. Aborting backup."
    exit 1
fi

# Check if the container is healthy
HEALTH_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "unknown")
if [ "$HEALTH_STATUS" != "healthy" ]; then
    log "WARN" "Container ${CONTAINER_NAME} health status is '${HEALTH_STATUS}'. Proceeding with backup anyway."
fi

# ---------------------------
# Perform backup
# ---------------------------
log "INFO" "Starting backup to ${BACKUP_FILE}"

if docker exec "$CONTAINER_NAME" pg_dump \
    -U "${POSTGRES_USER}" \
    -d "${POSTGRES_DB}" \
    --format=custom \
    --compress=6 \
    > "$BACKUP_FILE" 2>> "$LOG_FILE"; then

    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    log "INFO" "Backup completed successfully: $(basename "$BACKUP_FILE") (${BACKUP_SIZE})"
else
    log "ERROR" "pg_dump failed. Check container logs: docker logs ${CONTAINER_NAME}"
    rm -f "$BACKUP_FILE"
    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_EZ_CHAT_ID" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_EZ_CHAT_ID}" \
            --data-urlencode "text=🔴 BACKUP FAILED — pg_dump falló. Revisar logs." > /dev/null
    fi
    exit 1
fi

# ---------------------------
# Verify backup integrity
# ---------------------------
if [ ! -s "$BACKUP_FILE" ]; then
    log "ERROR" "Backup file is empty. Removing and aborting."
    rm -f "$BACKUP_FILE"
    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_EZ_CHAT_ID" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_EZ_CHAT_ID}" \
            --data-urlencode "text=🔴 BACKUP FAILED — archivo vacío. Revisar logs." > /dev/null
    fi
    exit 1
fi

# ---------------------------
# Rotate old backups
# ---------------------------
DELETED_COUNT=$(find "$BACKUP_DIR" -name "onnix_prod_*.dump" -type f -mtime +${RETENTION_DAYS} | wc -l)
find "$BACKUP_DIR" -name "onnix_prod_*.dump" -type f -mtime +${RETENTION_DAYS} -delete

if [ "$DELETED_COUNT" -gt 0 ]; then
    log "INFO" "Rotation: deleted ${DELETED_COUNT} backup(s) older than ${RETENTION_DAYS} days."
else
    log "INFO" "Rotation: no backups older than ${RETENTION_DAYS} days to delete."
fi

# ---------------------------
# Summary
# ---------------------------
TOTAL_BACKUPS=$(find "$BACKUP_DIR" -name "onnix_prod_*.dump" -type f | wc -l)
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)
log "INFO" "Backup complete. Total backups: ${TOTAL_BACKUPS}, total size: ${TOTAL_SIZE}"

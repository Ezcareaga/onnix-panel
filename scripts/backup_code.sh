#!/usr/bin/env bash
# =============================================================================
# Onnix SA — Daily Code & History Backup (CLEAN-10)
# Tars git history + planning + code, pushes to Drive. Companion to
# backup_db.sh (DB dump) and backup_gdrive.sh (working tree minus .git/.planning).
#
# Why this exists: backup_gdrive.sh explicitly excludes .git/** and .planning/**
# (lines 50, 58 of that script). Without this script, a VPS loss destroys all
# git history (commits, branches, tags) and all planning artifacts.
#
# Cron: 02:30 PYT daily.
# Retention on Drive: 90 days (auto-sweep at end of each run).
# =============================================================================

set -euo pipefail

# ---------------------------
# Configuration
# ---------------------------
# El código dejó de vivir en el home: /home/onnix quedó para el estado del
# servidor (.env, images/, logs/, backups/, .venv) y el repo vive en /srv/onnix.
PROJECT_DIR="${ONNIX_PROJECT_DIR:-/srv/onnix/dev}"
STATE_DIR="${ONNIX_STATE_DIR:-/home/onnix}"
LOG_FILE="${STATE_DIR}/logs/system/backup_code.log"
RCLONE_DEST="gdrive_backup:onnix-backups/code"
TIMESTAMP=$(date +%Y%m%d_%H%M)
TAR_FILE="/tmp/code-backup-${TIMESTAMP}.tar.gz"
RETENTION_DAYS=90

# ---------------------------
# Load environment (for Telegram alerts)
# ---------------------------
if [ -f "${STATE_DIR}/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "${STATE_DIR}/.env"
    set +a
fi

# ---------------------------
# Logging + alerting
# ---------------------------
mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$1] [backup_code] $2" >> "$LOG_FILE"
}

alert() {
    log "ERROR" "$1"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_EZ_CHAT_ID:-}" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_EZ_CHAT_ID}" \
            --data-urlencode "text=🔴 BACKUP_CODE FAILED — $1" > /dev/null
    fi
}

# ---------------------------
# Pre-flight: capture working tree state
# ---------------------------
cd "$PROJECT_DIR"

DIRTY=$(git status --porcelain 2>/dev/null | wc -l)
HEAD=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
log "INFO" "Iniciando backup de código — branch=${BRANCH} HEAD=${HEAD} dirty=${DIRTY}"

if [ "$DIRTY" -gt 0 ]; then
    log "WARN" "${DIRTY} archivo(s) sin commit — quedan como dirty state en el tar"
fi

# ---------------------------
# Build tar archive
# ---------------------------
log "INFO" "Construyendo tar: ${TAR_FILE}"
if ! tar czf "$TAR_FILE" \
    --exclude='node_modules' \
    --exclude='__pycache__' \
    --exclude='.venv' \
    --exclude='_wt' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache' \
    --exclude='.planning/phases/*/raw' \
    .git .planning docs panel scripts scrapers prompts 2>> "$LOG_FILE"; then
    alert "tar falló al empaquetar — ver ${LOG_FILE}"
    rm -f "$TAR_FILE"
    exit 1
fi

TAR_BYTES=$(stat -c %s "$TAR_FILE")
TAR_SIZE=$(du -h "$TAR_FILE" | cut -f1)
log "INFO" "Tar creado: ${TAR_SIZE} (${TAR_BYTES} bytes)"

# Sanity: tar must be > 1 MB (otherwise something is empty / wrong)
if [ "$TAR_BYTES" -lt 1048576 ]; then
    alert "Tar sospechosamente chico (${TAR_BYTES} bytes) — abortando"
    rm -f "$TAR_FILE"
    exit 1
fi

# ---------------------------
# Upload to Drive
# ---------------------------
log "INFO" "Subiendo a ${RCLONE_DEST}/"
if ! rclone copy "$TAR_FILE" "$RCLONE_DEST/" --log-file="$LOG_FILE" --log-level INFO 2>> "$LOG_FILE"; then
    alert "rclone copy falló — ver ${LOG_FILE}. Tar local NO eliminado: ${TAR_FILE}"
    exit 1
fi

# Verify presence on remote before deleting local copy
TAR_NAME=$(basename "$TAR_FILE")
if ! rclone lsf "$RCLONE_DEST/" --include "$TAR_NAME" 2>/dev/null | grep -q "$TAR_NAME"; then
    alert "Tar no aparece en Drive tras copy. Tar local conservado: ${TAR_FILE}"
    exit 1
fi
log "INFO" "Subida confirmada en Drive: ${TAR_NAME}"

# ---------------------------
# Cleanup local tar
# ---------------------------
rm -f "$TAR_FILE"

# ---------------------------
# Retention sweep on Drive (90 days)
# ---------------------------
log "INFO" "Sweep de tars > ${RETENTION_DAYS} días en Drive..."
SWEEP_BEFORE=$(rclone lsf "$RCLONE_DEST/" --include "code-backup-*.tar.gz" 2>/dev/null | wc -l)
if rclone delete "$RCLONE_DEST/" \
    --include "code-backup-*.tar.gz" \
    --min-age "${RETENTION_DAYS}d" \
    --log-file="$LOG_FILE" \
    --log-level INFO 2>> "$LOG_FILE"; then
    SWEEP_AFTER=$(rclone lsf "$RCLONE_DEST/" --include "code-backup-*.tar.gz" 2>/dev/null | wc -l)
    SWEPT=$(( SWEEP_BEFORE - SWEEP_AFTER ))
    log "INFO" "Sweep OK. Eliminados: ${SWEPT}. Tars vigentes (≤${RETENTION_DAYS}d): ${SWEEP_AFTER}"
else
    log "WARN" "Sweep falló (no-fatal — upload del día sí completó). Ver ${LOG_FILE}"
fi

# ---------------------------
# Summary
# ---------------------------
REMOTE_COUNT=$(rclone lsf "$RCLONE_DEST/" --include "code-backup-*.tar.gz" 2>/dev/null | wc -l)
log "INFO" "Completado. Tars en Drive: ${REMOTE_COUNT}, último: ${TAR_NAME} (${TAR_SIZE})"

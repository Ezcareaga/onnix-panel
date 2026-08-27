#!/usr/bin/env bash
# =============================================================================
# Onnix SA — Google Drive Full Backup
# Syncs /home/onnix/ to gdrive_backup:onnix-backups/
# Excludes: images, old dumps, tool caches, IDE files, temp files.
# Runs via cron at 02:00 UTC daily.
# =============================================================================

set -euo pipefail

# Desde el 2026-08-18 esto sincroniza SOLO el estado del servidor: .env, images/,
# logs/, backups/, .venv. El código salió del home y vive en /srv/onnix; su backup
# es backup_code.sh, más GitHub y el mirror de /srv/onnix/repo.git.
SRC="${ONNIX_STATE_DIR:-/home/onnix}/"
DEST="gdrive_backup:onnix-backups"
LOG_FILE="/home/onnix/logs/gdrive_backup.log"

log() {
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [${1}] [GDRIVE] ${2}" >> "$LOG_FILE"
}

# Pre-flight
if ! command -v rclone &>/dev/null; then
    log "ERROR" "rclone not installed"
    exit 1
fi

log "INFO" "Iniciando sync completo a Google Drive"

# ── Step 1: rclone sync with excludes ──
# --exclude "*.log": los .log vivos se escriben MIENTRAS rclone los sube y el
# tamano cambia a mitad de transferencia, asi que el sync entero muere con
# "corrupted on transfer: sizes differ N vs N+2". El caso peor no es casual:
# $LOG_FILE es logs/gdrive_backup.log, o sea que este script se sube su propio
# log en vivo en cada corrida. Los .log rotados (*.log.1, *.gz) ya estaban
# excluidos; faltaban los activos. Diagnosticado 2026-08-17.
#
# OJO con `sync`: borra en el destino lo que no exista en el origen. Por eso va
# --exclude "code/**": backup_code.sh escribe los tar.gz en
# gdrive_backup:onnix-backups/code, y como /home/onnix/code no existe,
# este sync le borraba la carpeta entera todas las noches. Verificado 2026-08-17:
# el remote no tenia ningun code/. Cualquier destino nuevo bajo onnix-backups/
# que no sea un espejo de $SRC necesita su propio --exclude aca.
# N8N fue descomisionado — backup_workflows.sh ya no se llama
# Todos los dumps locales se sincronizan (retención controlada por backup_db.sh)
# CLEAN-11: exclude secrets — never sync to remote (.docker/** already excluded below)
rclone sync "$SRC" "$DEST" \
    --exclude ".env" \
    --exclude ".env.*" \
    --exclude "*.env" \
    --exclude "*.key" \
    --exclude "*.pem" \
    --exclude "credentials.json" \
    --exclude "secrets/**" \
    --exclude "images/**" \
    --exclude "__pycache__/**" \
    --exclude ".tmp_workflows/**" \
    --exclude "node_modules/**" \
    --exclude "*.pyc" \
    --exclude ".vscode-server/**" \
    --exclude ".npm-global/**" \
    --exclude ".npm/**" \
    --exclude ".cache/**" \
    --exclude ".local/**" \
    --exclude ".claude/**" \
    --exclude ".claude.json" \
    --exclude ".venv/**" \
    --exclude "venv/**" \
    --exclude ".w3m/**" \
    --exclude ".n8n-mcp/**" \
    --exclude ".playwright-mcp/**" \
    --exclude ".pki/**" \
    --exclude ".docker/**" \
    --exclude ".git/**" \
    --exclude ".pytest_cache/**" \
    --exclude ".ssh/**" \
    --exclude ".google_authenticator" \
    --exclude ".bash_history" \
    --exclude ".tg_session*" \
    --exclude ".infocasas_session" \
    --exclude "screenshots/**" \
    --exclude ".planning/**" \
    --exclude ".lesshst" \
    --exclude "logs/**/*.log.[0-9]*" \
    --exclude "logs/**/*.gz" \
    --exclude "code/**" \
    --exclude "*.log" \
    --log-file="$LOG_FILE" \
    --log-level INFO \
    --transfers 4 \
    --checkers 8 \
    --ignore-checksum

log "INFO" "Sync completado"

# ── Step 3: Verify ──
REMOTE_SIZE=$(rclone size "$DEST" --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{d[\"bytes\"]/1024/1024:.1f} MB')" 2>/dev/null || echo "unknown")
log "INFO" "Tamano remoto total: ${REMOTE_SIZE}"

#!/usr/bin/env bash
# =============================================================================
# Onnix SA — Weekly Backup Restore Verification
# Runs every Sunday at 05:00 PYT (09:00 UTC).
# Takes the most recent dump, restores it to onnix_dev, and runs a
# sanity check. Sends a Telegram alert if anything fails.
# =============================================================================

set -euo pipefail

PROJECT_DIR="${ONNIX_STATE_DIR:-/home/onnix}"  # estado del servidor: .env, logs/, backups/
BACKUP_DIR="${PROJECT_DIR}/backups"
LOG_FILE="${PROJECT_DIR}/logs/system/verify_backup.log"
CONTAINER_NAME="onnix-postgres"
TMP_DUMP="/tmp/verify_restore_$$.dump"

# ---------------------------
# Load environment variables
# ---------------------------
if [ -f "${PROJECT_DIR}/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "${PROJECT_DIR}/.env"
    set +a
fi

log() {
    local level="$1"
    local message="$2"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] [verify_backup] ${message}" >> "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# compute_days_since_last_ok <log_file>
#
# Searches <log_file> and its rotations (.1, .2.gz, .3.gz, ...) for the most
# recent "Verificación completada exitosamente" line.
# Prints: "0", "N" (integer days), or "never".
# ---------------------------------------------------------------------------
compute_days_since_last_ok() {
    local target_log="${1:-$LOG_FILE}"
    local pattern="Verificación completada exitosamente"
    local last_ts=""

    # Collect candidate log files (current + rotated plain + rotated gzip)
    local log_files=()
    if [ -f "${target_log}" ]; then
        log_files+=("${target_log}")
    fi
    local i=1
    while [ -f "${target_log}.${i}" ]; do
        log_files+=("${target_log}.${i}")
        (( i++ )) || true
    done
    i=2
    while [ -f "${target_log}.${i}.gz" ]; do
        log_files+=("${target_log}.${i}.gz")
        (( i++ )) || true
    done

    if [ ${#log_files[@]} -eq 0 ]; then
        echo "never"
        return 0
    fi

    # zgrep handles plain and gzip files transparently.
    # Keep only the last (most recent) matching line across all files.
    # The pipeline may produce no output (no match) — || true prevents set -e exit.
    last_ts=$(zgrep -h "${pattern}" "${log_files[@]}" 2>/dev/null \
        | grep -oP '^\[\K[0-9]{4}-[0-9]{2}-[0-9]{2}' \
        | tail -1) || true

    if [ -z "${last_ts}" ]; then
        echo "never"
        return 0
    fi

    local last_epoch today_epoch days
    last_epoch=$(date -d "${last_ts}" +%s 2>/dev/null) || { echo "never"; return 0; }
    today_epoch=$(date -d "$(date +%Y-%m-%d)" +%s)
    days=$(( (today_epoch - last_epoch) / 86400 ))
    echo "${days}"
}

alert() {
    local message="$1"
    log "ERROR" "$message"

    local days
    days=$(compute_days_since_last_ok "${LOG_FILE}")
    local days_str
    if [ "${days}" = "never" ]; then
        days_str="nunca"
    else
        days_str="hace ${days} día(s)"
    fi

    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_EZ_CHAT_ID:-}" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_EZ_CHAT_ID}" \
            --data-urlencode "text=🔴 BACKUP VERIFY FAILED — ${message} (último OK ${days_str})" > /dev/null
    fi
}

# ---------------------------------------------------------------------------
# CLEAN-10: verify_code_backup
#
# Downloads the most recent code-backup-*.tar.gz from Drive, checks tar
# integrity, confirms key files (.git/HEAD, .git/refs/heads/master,
# .planning/STATE.md, panel/app/main.py, docs/RUNBOOK_RESTORE.md) are
# present, and that the tar is at most 30h old.
#
# On success: sets CODE_TAR_NAME global and returns 0.
# On failure: calls alert() with specific reason and returns 1.
# ---------------------------------------------------------------------------
verify_code_backup() {
    CODE_TAR_NAME=""
    local code_remote="gdrive_backup:onnix-backups/code"

    local latest_tar
    latest_tar=$(rclone lsf "${code_remote}/" --include "code-backup-*.tar.gz" 2>/dev/null | sort | tail -1)

    if [ -z "${latest_tar}" ]; then
        alert "No se encontró ningún code-backup-*.tar.gz en ${code_remote}/"
        return 1
    fi

    local tmp_tar="/tmp/verify_code_$$_${latest_tar}"
    if ! rclone copyto "${code_remote}/${latest_tar}" "${tmp_tar}" --log-file="${LOG_FILE}" --log-level INFO 2>> "${LOG_FILE}"; then
        alert "rclone copyto del code tar falló — ${latest_tar}"
        rm -f "${tmp_tar}"
        return 1
    fi

    # Age check via mtime on remote (rclone lsl prints: size date time path)
    local remote_ts
    remote_ts=$(rclone lsl "${code_remote}/${latest_tar}" 2>/dev/null | awk '{print $2" "$3}' | cut -d. -f1)
    if [ -n "${remote_ts}" ]; then
        local age_hours
        age_hours=$(( ( $(date +%s) - $(date -d "${remote_ts}" +%s 2>/dev/null || echo $(date +%s)) ) / 3600 ))
        log "INFO" "Code tar más reciente: ${latest_tar} (edad: ${age_hours}h)"
        if [ "${age_hours}" -gt 30 ]; then
            alert "Code tar ${latest_tar} tiene ${age_hours}h de antigüedad — posible falla en backup_code.sh"
            rm -f "${tmp_tar}"
            return 1
        fi
    else
        log "WARN" "No se pudo determinar la edad de ${latest_tar} desde rclone lsl — saltando age check"
    fi

    # Integrity check
    if ! tar tzf "${tmp_tar}" > /dev/null 2>> "${LOG_FILE}"; then
        alert "Code tar ${latest_tar} está corrupto (tar tzf falló)"
        rm -f "${tmp_tar}"
        return 1
    fi

    # Required files present (pipe tar tzf directly per check — avoids any
    # variable-storage issues with multi-MB listings; -Fx = literal full-line match)
    local missing=""
    for required in ".git/HEAD" ".git/refs/heads/master" ".planning/STATE.md" "panel/app/main.py" "docs/RUNBOOK_RESTORE.md"; do
        if ! tar tzf "${tmp_tar}" 2>/dev/null | grep -qFx "${required}"; then
            missing+="${required} "
        fi
    done

    rm -f "${tmp_tar}"

    if [ -n "${missing}" ]; then
        alert "Code tar ${latest_tar} le faltan archivos clave: ${missing}"
        return 1
    fi

    log "INFO" "Code tar OK — ${latest_tar} contiene .git + .planning + panel + docs"
    CODE_TAR_NAME="${latest_tar}"
    return 0
}

main() {
    log "INFO" "Iniciando verificación semanal de backup"

    # ---------------------------
    # Find most recent dump
    # ---------------------------
    LATEST_DUMP=$(find "${BACKUP_DIR}" -maxdepth 1 -name "onnix_prod_*.dump" -type f -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | head -1 | cut -d' ' -f2-)

    if [ -z "$LATEST_DUMP" ]; then
        alert "No se encontró ningún dump en ${BACKUP_DIR}"
        exit 1
    fi

    DUMP_NAME=$(basename "$LATEST_DUMP")
    DUMP_AGE_HOURS=$(( ( $(date +%s) - $(stat -c %Y "$LATEST_DUMP") ) / 3600 ))
    log "INFO" "Dump más reciente: ${DUMP_NAME} (edad: ${DUMP_AGE_HOURS}h)"

    if [ "$DUMP_AGE_HOURS" -gt 30 ]; then
        alert "El dump más reciente tiene ${DUMP_AGE_HOURS}h de antigüedad — posible falla en backup diario"
        exit 1
    fi

    # ---------------------------
    # Copy dump into container
    # ---------------------------
    log "INFO" "Copiando dump al contenedor..."
    if ! docker cp "$LATEST_DUMP" "${CONTAINER_NAME}:${TMP_DUMP}" 2>> "$LOG_FILE"; then
        alert "No se pudo copiar el dump al contenedor ${CONTAINER_NAME}"
        exit 1
    fi

    # ---------------------------
    # Drop & recreate onnix_dev (clean slate avoids --clean FK cascade issues)
    # ---------------------------
    log "INFO" "Verificando conexiones a onnix_dev..."

    # Abort if real application connections are active on staging (i.e. non-idle,
    # non-pg_restore). pg_restore workers left over from a previous failed run are
    # harmless zombies and must not block the retry — they will be terminated below.
    ACTIVE_CONN=$(docker exec "${CONTAINER_NAME}" psql -U "${POSTGRES_USER}" -d postgres -tAc \
        "SELECT COUNT(*) FROM pg_stat_activity
         WHERE datname='onnix_dev'
           AND state IN ('active','idle in transaction')
           AND application_name <> 'pg_restore'
           AND pid <> pg_backend_pid();" 2>/dev/null | tr -d ' \n')

    if [ -n "${ACTIVE_CONN}" ] && [ "${ACTIVE_CONN}" -gt 0 ]; then
        docker exec "${CONTAINER_NAME}" rm -f "$TMP_DUMP" 2>/dev/null || true
        alert "Staging DB tiene ${ACTIVE_CONN} conexión(es) activa(s), abortando para no interrumpir uso"
        exit 1
    fi

    # Terminate ALL remaining connections (idle panel-dev + any pg_restore zombies)
    # so dropdb can proceed without "database is being accessed" error.
    log "INFO" "Terminando conexiones a onnix_dev..."
    docker exec "${CONTAINER_NAME}" psql -U "${POSTGRES_USER}" -d postgres -tAc \
        "SELECT pg_terminate_backend(pid)
         FROM pg_stat_activity
         WHERE datname='onnix_dev'
           AND pid <> pg_backend_pid();" >> "$LOG_FILE" 2>&1 || true

    log "INFO" "Dropeando onnix_dev..."
    if ! docker exec "${CONTAINER_NAME}" dropdb -U "${POSTGRES_USER}" --if-exists onnix_dev >> "$LOG_FILE" 2>&1; then
        docker exec "${CONTAINER_NAME}" rm -f "$TMP_DUMP" 2>/dev/null || true
        alert "No se pudo dropear onnix_dev"
        exit 1
    fi

    log "INFO" "Creando onnix_dev..."
    # Use template0 as template to avoid collation-version mismatch warnings on
    # template1/postgres that can block createdb when glibc version differs.
    if ! docker exec "${CONTAINER_NAME}" createdb -U "${POSTGRES_USER}" -O "${POSTGRES_USER}" \
            -T template0 onnix_dev >> "$LOG_FILE" 2>&1; then
        docker exec "${CONTAINER_NAME}" rm -f "$TMP_DUMP" 2>/dev/null || true
        alert "No se pudo crear onnix_dev"
        exit 1
    fi

    # ---------------------------
    # Restore to clean database — no --clean needed, base is empty.
    # pg_restore exits non-zero if there are any warnings (e.g. FK constraint
    # violations from orphaned rows in prod). We capture the exit code, log it
    # as a warning, and let the sanity check below be the final success gate.
    # A truly catastrophic restore (no tables at all) will fail the sanity check.
    # ---------------------------
    log "INFO" "Restaurando en onnix_dev..."
    PG_RESTORE_RC=0
    docker exec "$CONTAINER_NAME" pg_restore \
            -U "${POSTGRES_USER}" \
            -d onnix_dev \
            --no-owner \
            "$TMP_DUMP" >> "$LOG_FILE" 2>&1 || PG_RESTORE_RC=$?

    docker exec "$CONTAINER_NAME" rm -f "$TMP_DUMP" 2>/dev/null || true

    if [ "${PG_RESTORE_RC}" -ne 0 ]; then
        log "WARNING" "pg_restore terminó con código ${PG_RESTORE_RC} (ver líneas anteriores). Ejecutando sanity check de todas formas."
    fi

    # ---------------------------
    # Sanity check
    # ---------------------------
    log "INFO" "Ejecutando sanity check..."
    PROP_COUNT=$(docker exec "$CONTAINER_NAME" psql -U "${POSTGRES_USER}" -d onnix_dev -t -c \
        "SELECT COUNT(*) FROM properties;" 2>/dev/null | tr -d ' \n')

    if [ -z "$PROP_COUNT" ] || [ "$PROP_COUNT" -lt 1000 ]; then
        alert "Sanity check fallido — properties count=${PROP_COUNT:-'error'} en onnix_dev"
        exit 1
    fi

    log "INFO" "Sanity check OK — properties en onnix_dev: ${PROP_COUNT}"
    log "INFO" "DB dump verificado OK: ${DUMP_NAME}"

    # CLEAN-10: code tar verification (must also pass for "Verificación completada exitosamente" gate)
    log "INFO" "Iniciando verificación de code tar..."
    if ! verify_code_backup; then
        # alert already sent inside verify_code_backup
        exit 1
    fi

    # Both checks passed — log the canonical success line that compute_days_since_last_ok() reads
    log "INFO" "Verificación completada exitosamente para dump: ${DUMP_NAME} + code tar: ${CODE_TAR_NAME}"

    # Success notification (optional, only if Telegram vars set)
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_EZ_CHAT_ID:-}" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_EZ_CHAT_ID}" \
            --data-urlencode "text=✅ Backup verificado OK — DB ${DUMP_NAME} (${PROP_COUNT} properties) + code tar ${CODE_TAR_NAME}" > /dev/null
    fi
}

# ---------------------------------------------------------------------------
# Entry point guard — allows script to be sourced for testing without
# executing the main restore flow.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main
fi

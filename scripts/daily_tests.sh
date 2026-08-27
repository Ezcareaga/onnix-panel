#!/bin/bash
# Onnix SA — Daily E2E Tests (All Suites)
# Runs test_bot.py + test_geo_central.py + test_geo_interior.py + test_v5_db.py
# 58 + 7 v5 DB tests (v5 DB suite verifies WA-feature artifacts in DB state —
# live WA E2E requires manual test per Phase 49 checkpoint)
# Cron: 0 7 * * * (7:00 UTC = ~4:00 AM PYT)

PROJECT_DIR="${ONNIX_STATE_DIR:-/home/onnix}"  # estado del servidor: .env, logs/, backups/
LOGDIR="$PROJECT_DIR/logs/tests"
LOG_SCRIPT="$(dirname "${BASH_SOURCE[0]}")/log.sh"
TIMESTAMP=$(date +%Y-%m-%d_%H%M)

# Load env (for TELEGRAM_BOT_TOKEN, TELEGRAM_EZ_CHAT_ID, TG_API_ID, etc.)
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi

cd "$PROJECT_DIR"

# Run all 4 test suites
TOTAL_FAIL=0

# Clean stale Telethon session journal before first Telethon suite
rm -f /home/onnix/.tg_session.session-journal

python3 scripts/test_bot.py > "$LOGDIR/test_run_bot_$TIMESTAMP.log" 2>&1
EXIT_BOT=$?
[ $EXIT_BOT -ne 0 ] && TOTAL_FAIL=$((TOTAL_FAIL + 1))

# Cooldown + clean session journal between Telethon suites
sleep 10
rm -f /home/onnix/.tg_session.session-journal

python3 scripts/test_geo_central.py > "$LOGDIR/test_run_geo_central_$TIMESTAMP.log" 2>&1
EXIT_GEO_C=$?
[ $EXIT_GEO_C -ne 0 ] && TOTAL_FAIL=$((TOTAL_FAIL + 1))

# Cooldown + clean session journal between Telethon suites
sleep 10
rm -f /home/onnix/.tg_session.session-journal

python3 scripts/test_geo_interior.py > "$LOGDIR/test_run_geo_interior_$TIMESTAMP.log" 2>&1
EXIT_GEO_I=$?
[ $EXIT_GEO_I -ne 0 ] && TOTAL_FAIL=$((TOTAL_FAIL + 1))

# v5 DB suite uses no Telethon — no journal cleanup needed
python3 scripts/test_v5_db.py > "$LOGDIR/test_run_v5_db_$TIMESTAMP.log" 2>&1
EXIT_V5=$?
[ $EXIT_V5 -ne 0 ] && TOTAL_FAIL=$((TOTAL_FAIL + 1))

if [ $TOTAL_FAIL -gt 0 ]; then
    # Build failure summary from all failing suites
    FAILS=""
    [ $EXIT_BOT -ne 0 ] && FAILS="${FAILS}BOT: $(grep -E '❌|FAIL' "$LOGDIR/test_run_bot_$TIMESTAMP.log" | head -3)\n"
    [ $EXIT_GEO_C -ne 0 ] && FAILS="${FAILS}GEO_CENTRAL: $(grep -E '❌|FAIL' "$LOGDIR/test_run_geo_central_$TIMESTAMP.log" | head -3)\n"
    [ $EXIT_GEO_I -ne 0 ] && FAILS="${FAILS}GEO_INTERIOR: $(grep -E '❌|FAIL' "$LOGDIR/test_run_geo_interior_$TIMESTAMP.log" | head -3)\n"
    [ $EXIT_V5 -ne 0 ] && FAILS="${FAILS}V5_DB: $(grep -E 'FAIL' "$LOGDIR/test_run_v5_db_$TIMESTAMP.log" | head -3)\n"

    MESSAGE="🔴 TESTS E2E FALLARON ($TIMESTAMP)
bot=${EXIT_BOT} geo_central=${EXIT_GEO_C} geo_interior=${EXIT_GEO_I} v5_db=${EXIT_V5}
${FAILS}Ver logs: test_run_*_$TIMESTAMP.log"

    # Alert via Telegram
    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_EZ_CHAT_ID" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_EZ_CHAT_ID}" \
            --data-urlencode "text=${MESSAGE}" > /dev/null
    fi

    $LOG_SCRIPT WARN TESTS "Daily E2E tests FAILED ($TOTAL_FAIL/4 suites)" "{\"bot\": $EXIT_BOT, \"geo_central\": $EXIT_GEO_C, \"geo_interior\": $EXIT_GEO_I, \"v5_db\": $EXIT_V5}"
else
    $LOG_SCRIPT INFO TESTS "Daily E2E tests PASSED (4/4 suites, 65 tests)" "{\"bot\": 0, \"geo_central\": 0, \"geo_interior\": 0, \"v5_db\": 0}"
fi

# Clean old test logs (keep 14 days)
find "$LOGDIR" -name "test_run_*.log" -mtime +14 -delete
find "$LOGDIR" -name "test_report_*.json" -mtime +14 -delete
find "$LOGDIR" -name "test_report_*.md" -mtime +14 -delete
find "$LOGDIR" -name "test_v5_db_*.json" -mtime +14 -delete
find "$LOGDIR" -name "test_v5_db_*.md" -mtime +14 -delete

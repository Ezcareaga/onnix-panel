#!/bin/bash
# Onnix SA — Unified Test Runner
# Runs all 4 test suites sequentially and produces consolidated report.
# Usage: ./scripts/run_all_tests.sh
#
# Suites:
#   1. test_bot.py          — 45 tests (main E2E: saludo, búsqueda, wizard, callbacks, geo)
#   2. test_geo_central.py  — 7 tests  (geography Central: landmarks, city search, zonas cercanas)
#   3. test_geo_interior.py — 6 tests  (geography Interior: Encarnación, CDE, aliases)
#   4. test_v5_db.py        — 7 tests  (v5 DB verification: ContentSids, schema, API key)
#
# Total: 65 tests

set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGS_DIR="/home/onnix/logs/tests"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$LOGS_DIR"

echo ""
echo "============================================================"
echo "  ONNIX SA — UNIFIED TEST RUNNER"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# Track results
SUITE_RESULTS=()
TOTAL_PASS=0
TOTAL_FAIL=0
TOTAL_TESTS=0
ALL_PASSED=true

run_suite() {
    local name="$1"
    local script="$2"
    local expected="$3"

    echo ""
    echo "────────────────────────────────────────────────────────────"
    echo "  SUITE: $name ($expected tests)"
    echo "────────────────────────────────────────────────────────────"

    # Clean stale Telethon session journal to prevent SQLite lock errors
    rm -f /home/onnix/.tg_session.session-journal

    local exit_code=0
    python3 "$SCRIPTS_DIR/$script" || exit_code=$?

    if [ $exit_code -eq 0 ]; then
        SUITE_RESULTS+=("PASS|$name|$expected")
        TOTAL_PASS=$((TOTAL_PASS + expected))
    elif [ $exit_code -eq 2 ]; then
        echo "  ERROR: Telegram session not authorized"
        SUITE_RESULTS+=("ERROR|$name|0")
        ALL_PASSED=false
        return 1
    else
        # Parse actual pass/fail from the latest JSON report
        local latest_json
        latest_json=$(ls -t "$LOGS_DIR"/*.json 2>/dev/null | head -1)
        if [ -n "$latest_json" ]; then
            local passed failed
            passed=$(python3 -c "import json; d=json.load(open('$latest_json')); print(d.get('passed',0))" 2>/dev/null || echo 0)
            failed=$(python3 -c "import json; d=json.load(open('$latest_json')); print(d.get('failed',0))" 2>/dev/null || echo 0)
            SUITE_RESULTS+=("FAIL|$name|$passed/$((passed+failed))")
            TOTAL_PASS=$((TOTAL_PASS + passed))
            TOTAL_FAIL=$((TOTAL_FAIL + failed))
        else
            SUITE_RESULTS+=("FAIL|$name|?")
            TOTAL_FAIL=$((TOTAL_FAIL + expected))
        fi
        ALL_PASSED=false
    fi

    TOTAL_TESTS=$((TOTAL_TESTS + expected))
    return 0
}

# Run suites sequentially (they share the Telegram session)
run_suite "Main E2E" "test_bot.py" 45 || true
echo ""
echo "  Cooldown 10s between suites..."
sleep 10

run_suite "Geography Central" "test_geo_central.py" 7 || true
echo ""
echo "  Cooldown 10s between suites..."
sleep 10

run_suite "Geography Interior" "test_geo_interior.py" 6 || true

# v5 DB suite (no Telethon, no cooldown needed)
run_suite "v5 DB Verification" "test_v5_db.py" 7 || true

# ============================================================
# CONSOLIDATED REPORT
# ============================================================
echo ""
echo "============================================================"
echo "  CONSOLIDATED RESULTS"
echo "============================================================"
echo ""

printf "  %-25s %-8s %s\n" "Suite" "Status" "Tests"
printf "  %-25s %-8s %s\n" "─────────────────────────" "────────" "─────"
for result in "${SUITE_RESULTS[@]}"; do
    IFS='|' read -r status name tests <<< "$result"
    if [ "$status" = "PASS" ]; then
        icon="✅"
    else
        icon="❌"
    fi
    printf "  %-25s %s %-6s %s\n" "$name" "$icon" "$status" "$tests"
done

echo ""
if $ALL_PASSED; then
    echo "  TOTAL: $TOTAL_PASS/$TOTAL_TESTS PASS ✅"
else
    echo "  TOTAL: $TOTAL_PASS/$TOTAL_TESTS PASS, $TOTAL_FAIL/$TOTAL_TESTS FAIL ❌"
fi
echo ""

# Write consolidated JSON report
REPORT_PATH="$LOGS_DIR/test_consolidated_${TIMESTAMP}.json"
python3 -c "
import json, glob, os

# Find latest report from each suite
reports = {}
for pattern, key in [('test_report_*.json', 'main'), ('test_geo_central_*.json', 'central'), ('test_geo_interior_*.json', 'interior'), ('test_v5_db_*.json', 'v5_db')]:
    files = sorted(glob.glob(os.path.join('$LOGS_DIR', pattern)), reverse=True)
    if files:
        with open(files[0]) as f:
            reports[key] = json.load(f)

# Consolidate
all_tests = []
total_pass = 0
total_fail = 0
for key in ['main', 'central', 'interior', 'v5_db']:
    if key in reports:
        all_tests.extend(reports[key].get('tests', []))
        total_pass += reports[key].get('passed', 0)
        total_fail += reports[key].get('failed', 0)

consolidated = {
    'timestamp': '$(date -Iseconds)',
    'version': 'consolidated-v1',
    'suites': {k: {'passed': v.get('passed',0), 'failed': v.get('failed',0), 'total': v.get('total',0)} for k,v in reports.items()},
    'total': total_pass + total_fail,
    'passed': total_pass,
    'failed': total_fail,
    'tests': all_tests
}

with open('$REPORT_PATH', 'w') as f:
    json.dump(consolidated, f, indent=2, ensure_ascii=False)
print(f'  Report: $REPORT_PATH')
" 2>/dev/null || echo "  WARN: Could not write consolidated JSON report"

# Write consolidated MD report
MD_PATH="${REPORT_PATH%.json}.md"
python3 -c "
import json
with open('$REPORT_PATH') as f:
    data = json.load(f)

with open('$MD_PATH', 'w') as f:
    f.write('# Consolidated Test Report — $(date '+%Y-%m-%d %H:%M')\n\n')
    f.write(f'**Result: {data[\"passed\"]}/{data[\"total\"]} PASS, {data[\"failed\"]}/{data[\"total\"]} FAIL**\n\n')

    f.write('## Suites\n\n')
    f.write('| Suite | Passed | Failed | Total |\n')
    f.write('|-------|--------|--------|-------|\n')
    for name, s in data['suites'].items():
        status = '✅' if s['failed'] == 0 else '❌'
        f.write(f'| {name} | {s[\"passed\"]} | {s[\"failed\"]} | {s[\"total\"]} {status} |\n')

    f.write('\n## All Tests\n\n')
    f.write('| # | Test | Status | Notes |\n')
    f.write('|---|------|--------|-------|\n')
    for t in data['tests']:
        icon = '✅' if t['status'] == 'PASS' else '❌'
        notes = t.get('notes', '')
        f.write(f'| {t[\"id\"]} | {t[\"name\"]} | {icon} {t[\"status\"]} | {notes} |\n')

    failures = [t for t in data['tests'] if t['status'] == 'FAIL']
    if failures:
        f.write('\n## Failures\n\n')
        for t in failures:
            f.write(f'### {t[\"id\"]}: {t[\"name\"]}\n')
            f.write(f'- **Expected:** {t[\"expected\"]}\n')
            f.write(f'- **Actual:** {t[\"actual\"]}\n')
            if t.get('notes'):
                f.write(f'- **Notes:** {t[\"notes\"]}\n')
            f.write('\n')

print(f'  Report MD: $MD_PATH')
" 2>/dev/null || echo "  WARN: Could not write consolidated MD report"

echo ""
echo "============================================================"

# Log
"$SCRIPTS_DIR/log.sh" INFO TESTS \
    "Consolidated E2E: $TOTAL_PASS/$TOTAL_TESTS PASS, $TOTAL_FAIL/$TOTAL_TESTS FAIL" 2>/dev/null || true

# Exit with appropriate code
if $ALL_PASSED; then
    exit 0
else
    exit 1
fi

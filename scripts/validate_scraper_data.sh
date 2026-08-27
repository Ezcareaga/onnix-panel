#!/bin/bash
# Onnix SA — Post-Scraper Data Validation
# Valida integridad de datos después de que todos los scrapers terminan.
# Cron: 0 6 * * * (6:00 UTC = ~3:00 AM PYT)

PROJECT_DIR="${ONNIX_STATE_DIR:-/home/onnix}"  # estado del servidor: .env, logs/, backups/
LOG_SCRIPT="$(dirname "${BASH_SOURCE[0]}")/log.sh"
COUNTS_FILE="$PROJECT_DIR/logs/property_counts.json"
ALERTS=""

# Load env
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi

# Helper: run SQL query and return trimmed result
run_sql() {
    docker exec onnix-postgres psql -U onnix -d onnix_prod -t -A -c "$1" 2>/dev/null | tr -d ' \n'
}

run_sql_rows() {
    docker exec onnix-postgres psql -U onnix -d onnix_prod -t -A -c "$1" 2>/dev/null
}

# =========================================================
# CHECK 1 — Property_type en inglés (regresión normalization)
# =========================================================
ENGLISH_TYPES=$(run_sql "
SELECT COUNT(*) FROM properties
WHERE property_type IN (
    'business','hotel','duplex','accommodation','office space','building floor',
    'health clinic','sports centre','hospital','plaza floor','gas station',
    'habitacion','triplex','resort style home','industria','farm for agriculture',
    'granja agricola','storage condo','garage','semi-detached apartment',
    'penthouse','boutique hotel'
);
")

if [ -n "$ENGLISH_TYPES" ] && [ "$ENGLISH_TYPES" -gt 0 ] 2>/dev/null; then
    DETAIL=$(run_sql_rows "
SELECT source || ': ' || property_type || ' (' || COUNT(*) || ')'
FROM properties
WHERE property_type IN (
    'business','hotel','duplex','accommodation','office space','building floor',
    'health clinic','sports centre','hospital','plaza floor','gas station',
    'habitacion','triplex','resort style home','industria','farm for agriculture',
    'granja agricola','storage condo','garage','semi-detached apartment',
    'penthouse','boutique hotel'
)
GROUP BY source, property_type LIMIT 5;
" | tr '\n' ', ')
    ALERTS="${ALERTS}⚠️ property_type en inglés: ${ENGLISH_TYPES} props — ${DETAIL}\n"
fi

# =========================================================
# CHECK 2 — Geo invertidos REMAX (regresión)
# =========================================================
GEO_INVERTED=$(run_sql "
SELECT COUNT(*) FROM properties
WHERE source = 'remax' AND neighborhood = 'Asunción' AND city != 'Asunción';
")

if [ -n "$GEO_INVERTED" ] && [ "$GEO_INVERTED" -gt 0 ] 2>/dev/null; then
    ALERTS="${ALERTS}⚠️ Geo invertidos REMAX: ${GEO_INVERTED} props con neighborhood=Asunción, city!=Asunción\n"
fi

DEPT_AS_NEIGHBORHOOD=$(run_sql "
SELECT COUNT(*) FROM properties
WHERE source = 'remax' AND neighborhood IN (
    'Central','Itapúa','Alto Paraná','Cordillera','Boquerón','Presidente Hayes',
    'Paraguarí','Misiones','Caaguazú','San Pedro','Guairá','Caazapá','Ñeembucú',
    'Amambay','Canindeyú','Concepción','Alto Paraguay'
);
")

if [ -n "$DEPT_AS_NEIGHBORHOOD" ] && [ "$DEPT_AS_NEIGHBORHOOD" -gt 0 ] 2>/dev/null; then
    ALERTS="${ALERTS}⚠️ neighborhood=departamento REMAX: ${DEPT_AS_NEIGHBORHOOD} props\n"
fi

# =========================================================
# CHECK 3 — Caída abrupta de propiedades por source
# =========================================================
# Get current counts per source
CURRENT_COUNTS=$(run_sql_rows "
SELECT source || '|' || COUNT(*) FROM properties
WHERE is_active = true
GROUP BY source ORDER BY source;
")

# Build JSON with current counts
NEW_JSON="{"
FIRST=true
while IFS='|' read -r src cnt; do
    [ -z "$src" ] && continue
    if [ "$FIRST" = true ]; then
        FIRST=false
    else
        NEW_JSON="${NEW_JSON},"
    fi
    NEW_JSON="${NEW_JSON}\"${src}\":${cnt}"
done <<< "$CURRENT_COUNTS"
NEW_JSON="${NEW_JSON}}"

# Compare with yesterday's counts
if [ -f "$COUNTS_FILE" ]; then
    # Use python for JSON comparison (simpler and safer)
    DROP_ALERT=$(python3 -c "
import json, sys
try:
    with open('$COUNTS_FILE') as f:
        old = json.load(f)
    new = json.loads('$NEW_JSON')
    alerts = []
    for src, old_cnt in old.items():
        new_cnt = new.get(src, 0)
        if old_cnt > 0:
            drop_pct = ((old_cnt - new_cnt) / old_cnt) * 100
            if drop_pct > 20:
                alerts.append(f'{src} cayó de {old_cnt} a {new_cnt} (-{drop_pct:.0f}%)')
    if alerts:
        print('; '.join(alerts))
except Exception as e:
    print(f'error: {e}', file=sys.stderr)
" 2>/dev/null)

    if [ -n "$DROP_ALERT" ]; then
        ALERTS="${ALERTS}🔴 Caída abrupta: ${DROP_ALERT}\n"
    fi
fi

# Save current counts for tomorrow's comparison
echo "$NEW_JSON" > "$COUNTS_FILE"

# =========================================================
# CHECK 4 — Property_type NULL en propiedades nuevas (24h)
# =========================================================
NULL_TYPES=$(run_sql_rows "
SELECT source || ': ' || COUNT(*)
FROM properties
WHERE property_type IS NULL AND created_at > NOW() - INTERVAL '24 hours'
GROUP BY source
HAVING COUNT(*) > 10;
")

if [ -n "$NULL_TYPES" ]; then
    NULL_DETAIL=$(echo "$NULL_TYPES" | tr '\n' ', ')
    ALERTS="${ALERTS}⚠️ property_type NULL nuevos (>10): ${NULL_DETAIL}\n"
fi

# =========================================================
# REPORT
# =========================================================
if [ -n "$ALERTS" ]; then
    $LOG_SCRIPT WARN SCRAPER_VALIDATION "Checks failed" "{\"alerts\": \"$(echo -e "$ALERTS" | tr '\n' ' ')\"}"

    MESSAGE="⚠️ SCRAPER VALIDATION ALERT
$(echo -e "$ALERTS")"

    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_EZ_CHAT_ID" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_EZ_CHAT_ID}" \
            --data-urlencode "text=${MESSAGE}" > /dev/null
    fi
else
    $LOG_SCRIPT INFO SCRAPER_VALIDATION "All checks passed"
fi

#!/bin/bash
# Check Twilio account balance — corre semanalmente (lunes 09:00 PYT)
# Alerta si balance < $5

PROJECT_DIR="${ONNIX_STATE_DIR:-/home/onnix}"  # estado del servidor: .env, logs/, backups/

# Load env
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi

if [ -z "$TWILIO_ACCOUNT_SID" ] || [ -z "$TWILIO_AUTH_TOKEN" ]; then
    echo "[$(date)] check_twilio_balance: TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN not set" >> "$PROJECT_DIR/logs/system/system.log"
    exit 0
fi

BALANCE_JSON=$(curl -s -u "${TWILIO_ACCOUNT_SID}:${TWILIO_AUTH_TOKEN}" \
    "https://api.twilio.com/2010-04-01/Accounts/${TWILIO_ACCOUNT_SID}/Balance.json" 2>/dev/null)

if [ -z "$BALANCE_JSON" ]; then
    echo "[$(date)] check_twilio_balance: API call failed" >> "$PROJECT_DIR/logs/system/system.log"
    exit 0
fi

BALANCE=$(echo "$BALANCE_JSON" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get('balance', '0'))
except Exception:
    print('error')
" 2>/dev/null)

CURRENCY=$(echo "$BALANCE_JSON" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get('currency', 'USD'))
except Exception:
    print('USD')
" 2>/dev/null)

if [ "$BALANCE" = "error" ]; then
    echo "[$(date)] check_twilio_balance: failed to parse balance" >> "$PROJECT_DIR/logs/system/system.log"
    exit 0
fi

echo "[$(date)] check_twilio_balance: balance=${BALANCE} ${CURRENCY}" >> "$PROJECT_DIR/logs/system/system.log"

# Alert if balance < 5
IS_LOW=$(python3 -c "print('yes' if float('$BALANCE') < 5 else 'no')" 2>/dev/null)

if [ "$IS_LOW" = "yes" ] && [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_EZ_CHAT_ID" ]; then
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TELEGRAM_EZ_CHAT_ID}" \
        --data-urlencode "text=⚠️ SALDO TWILIO BAJO: \$${BALANCE} ${CURRENCY}
Recargar para evitar interrupciones en WhatsApp." > /dev/null
fi

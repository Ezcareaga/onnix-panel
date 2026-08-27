#!/bin/bash
# Onnix SA — Script de Logging Centralizado
# Uso: ./log.sh <NIVEL> <MODULO> <MENSAJE> [JSON_CONTEXT]
# Ejemplo: ./log.sh INFO BOT "Saludo enviado" '{"contact_id": 123}'

NIVEL="${1:-INFO}"
MODULO="${2:-SYSTEM}"
MENSAJE="${3:-No message}"
CONTEXTO="${4:-}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Determinar archivo de log por módulo
case "$MODULO" in
  BOT|CLASSIFY|SEARCH|GENERATE|REPLY)
    LOGFILE="/home/onnix/logs/bot/bot.log"
    ;;
  INFOCASAS|IC_POLL|IC_PARSE|IC_LOGIN)
    LOGFILE="/home/onnix/logs/bot/infocasas.log"
    ;;
  COLDCHECK)
    LOGFILE="/home/onnix/logs/system/system.log"
    ;;
  *)
    LOGFILE="/home/onnix/logs/system/system.log"
    ;;
esac

# Construir línea de log
if [ -n "$CONTEXTO" ]; then
  LINE="[$TIMESTAMP] [$NIVEL] [$MODULO] $MENSAJE - $CONTEXTO"
else
  LINE="[$TIMESTAMP] [$NIVEL] [$MODULO] $MENSAJE"
fi

# Escribir al log del módulo
echo "$LINE" >> "$LOGFILE"

# Si es ERROR o CRITICAL, también escribir al error.log aggregado
if [ "$NIVEL" = "ERROR" ] || [ "$NIVEL" = "CRITICAL" ]; then
  echo "$LINE" >> "/home/onnix/logs/bot/error.log"
fi

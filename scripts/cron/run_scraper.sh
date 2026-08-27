#!/bin/bash
# Corre un scraper de captación desde el cron.
#
# Uso: run_scraper.sh <remax|psir|coldwell|onnixpy|infocasas> [args]
#
# Un solo lugar para el preflight, el timeout y el log, para que las cinco
# líneas del crontab sean idénticas salvo el nombre del portal. El preflight no
# es decoración: onnix.com.py dropea los SYN de la IP de este VPS desde el
# 19/08, y sin él la corrida gasta horas en ConnectTimeout, reintenta cinco veces
# por URL y deja un log donde no se distingue "el portal me bloquea" de "el
# parser se rompió".
#
# NUNCA pasarle --limit desde el cron: el scraper termina llamando a
# mark_inactive() con solo los IDs que vio, y el resto del catálogo de ese portal
# queda a un piso de cobertura de distancia de apagarse.
set -uo pipefail

PORTAL="${1:?uso: run_scraper.sh <remax|psir|coldwell|onnixpy|infocasas> [args]}"
shift

CODE_DIR="${ONNIX_CODE_DIR:-/srv/onnix/prod}"
STATE_DIR="${ONNIX_STATE_DIR:-/home/onnix}"
PYTHON="${ONNIX_PYTHON:-$STATE_DIR/.venv/bin/python}"
MAX_RUNTIME="${ONNIX_SCRAPER_TIMEOUT:-4h}"

# infocasas es el único cuyo ejecutable no es scrapers/<portal>.py: su código
# vive en un paquete, scrapers/infocasas/scraper.py. Y su host de preflight no
# es el del portal: desde el 2026-08-24 lee la API de oficina virtual en
# graph.infocasas.com.uy, así que chequear www.infocasas.com.py daría verde
# mientras el endpoint que de verdad usa está caído.
SCRIPT="scrapers/$PORTAL.py"
case "$PORTAL" in
    remax)       HOST=www.remax.com.py ;;
    psir)        HOST=www.psir.com.py ;;
    coldwell)    HOST=coldwellbanker.com.py ;;
    onnixpy) HOST=onnix.com.py ;;
    infocasas)   HOST=graph.infocasas.com.uy; SCRIPT=scrapers/infocasas/scraper.py ;;
    *) echo "portal desconocido: $PORTAL" >&2; exit 2 ;;
esac

LOG_DIR="$STATE_DIR/logs/scrapers"
LOG="$LOG_DIR/$PORTAL.log"
mkdir -p "$LOG_DIR"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

if ! timeout 8 bash -c "exec 3<>/dev/tcp/$HOST/443" 2>/dev/null; then
    echo "[$(ts)] [$PORTAL] $HOST no acepta TCP desde esta IP — corrida salteada" >> "$LOG"
    exit 0
fi

echo "[$(ts)] [$PORTAL] inicio" >> "$LOG"

cd "$CODE_DIR" || { echo "[$(ts)] [$PORTAL] no existe $CODE_DIR" >> "$LOG"; exit 1; }
# El de infocasas necesita escribir cookie de sesión y perfil de Chromium; sin
# esto los deja dentro del árbol de código, que es compartido y de sólo lectura.
export ONNIX_STATE_DIR="$STATE_DIR"
timeout "$MAX_RUNTIME" "$PYTHON" "$SCRIPT" "$@" >> "$LOG" 2>&1
rc=$?

if [ $rc -eq 124 ]; then
    echo "[$(ts)] [$PORTAL] cortado por timeout de $MAX_RUNTIME" >> "$LOG"
fi
echo "[$(ts)] [$PORTAL] fin rc=$rc" >> "$LOG"
exit $rc

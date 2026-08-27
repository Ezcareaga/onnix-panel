#!/usr/bin/env bash
# Corre la suite de pytest tomando el lock de la base de test.
#
# La suite entera apunta a onnix_dev y se pisa a sí misma si corren dos
# sesiones a la vez (67 fallos fantasma, ya pasó). El pipeline de CI toma este
# MISMO lock, así que un deploy y un humano nunca corren pytest en paralelo:
# el segundo espera y lo dice.
#
# Uso:  scripts/run_suite.sh [args de pytest]
# Sin args corre las DOS suites: panel/tests/ y tests/ (scrapers).
# Con args corre sólo lo pedido, desde panel/.
set -euo pipefail

LOCK=${ONNIX_SUITE_LOCK:-/var/lock/onnix-suite.lock}
WAIT=${ONNIX_SUITE_LOCK_WAIT:-1800}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VENV=${ONNIX_VENV:-/home/onnix/.venv}

exec 9>"$LOCK"
if ! flock -n 9; then
    holder=$(fuser "$LOCK" 2>/dev/null | tr -s ' ' | tr ' ' '\n' | grep -m1 '[0-9]' || true)
    echo "esperando el lock de la suite ($LOCK, lo tiene PID ${holder:-?})" >&2
    if ! flock -w "$WAIT" 9; then
        echo "timeout de ${WAIT}s esperando el lock de la suite — no se corrió nada" >&2
        exit 75
    fi
fi

set -a
# shellcheck disable=SC1091
. "$ROOT/.env"
set +a
export POSTGRES_HOST=127.0.0.1
export PATH="$VENV/bin:$PATH"

if [ $# -gt 0 ]; then
    # Con args explícitos corre SÓLO lo pedido, desde panel/ como siempre.
    cd "$ROOT/panel"
    exec "$VENV/bin/pytest" "$@"
fi

# -n 3 sobre 4 vCPU: la suite baja de 11:39 a 4:20. El cuarto núcleo queda
# para Postgres, que atiende a los tres workers desde el mismo contenedor.
# Cada worker usa su propia base (ver tests/conftest.py); si dos terminan
# apuntando a la misma, el flock del conftest aborta ruidoso.
cd "$ROOT/panel"
# `set -e` mataría el script en el primer rojo y la segunda suite no correría:
# el `|| rc=$?` es lo que hace que se corran las dos.
rc_panel=0
"$VENV/bin/pytest" -q --timeout=120 -p no:cacheprovider -p no:randomly \
    -n "${ONNIX_WORKERS:-3}" --dist loadfile || rc_panel=$?

# La suite de scrapers vive en tests/ y hasta el 2026-08-23 NO la corría nadie:
# `panel/pytest.ini` fija `testpaths = tests` y este script hacía `cd panel`, así
# que 16 archivos y ~470 tests quedaban fuera del gate. Se corre aparte, en
# serie —tarda ~1:10— y contra su propia base, que su conftest construye.
echo "--- suite de scrapers (tests/) ---"
cd "$ROOT"
rc_scrapers=0
"$VENV/bin/pytest" tests/ -q --timeout=120 -p no:cacheprovider -p no:randomly || rc_scrapers=$?

# El primero que falla es el que se reporta; los dos se corren igual, porque un
# rojo en panel no dice nada sobre scrapers.
if [ "$rc_panel" -ne 0 ]; then
    exit "$rc_panel"
fi
exit "$rc_scrapers"

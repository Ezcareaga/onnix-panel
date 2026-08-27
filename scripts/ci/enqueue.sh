#!/usr/bin/env bash
# Lo ejecuta `webhook` como el usuario onnixci, que no tiene sudo ni docker.
# Su único privilegio es escribir un archivo en la cola. Nada más.
#
# El archivo se llama <rama>.job — uno por rama, no por commit — así que tres
# pushes durante una corrida dejan UN job con el SHA de la punta (coalescing).
set -euo pipefail

QUEUE=${ONNIX_QUEUE:-/var/lib/onnixci/queue}
PROD_SWITCH=${ONNIX_PROD_SWITCH:-/etc/onnixci/prod_enabled}

ref=${1:-}
sha=${2:-}
actor=${3:-}
delivery=${4:-}

case "$ref" in
    refs/heads/dev)    branch=dev ;;
    refs/heads/master) branch=master ;;
    *) echo "ref no manejado: ${ref:-<vacio>}" >&2; exit 1 ;;
esac

if ! [[ "$sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "sha malformado: ${sha:-<vacio>}" >&2
    exit 1
fi

# Interruptor de deploy a producción. Mientras el archivo no exista, un push a
# master no encola nada — el pipeline de prod se habilita a mano, después del QA.
if [ "$branch" = master ] && [ ! -e "$PROD_SWITCH" ]; then
    echo "deploy a produccion deshabilitado (falta $PROD_SWITCH)" >&2
    exit 1
fi

# Todo lo que viene del payload y termina en un archivo va sin caracteres raros.
actor=${actor//[^A-Za-z0-9._-]/}
delivery=${delivery//[^A-Za-z0-9-]/}

tmp=$(mktemp "$QUEUE/.tmp.XXXXXX")
printf 'sha=%s\nbranch=%s\nactor=%s\ndelivery=%s\n' \
    "$sha" "$branch" "${actor:-desconocido}" "${delivery:-sin-id}" > "$tmp"
chmod 644 "$tmp"
mv -f "$tmp" "$QUEUE/$branch.job"

echo "encolado $branch $sha"

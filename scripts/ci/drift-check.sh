#!/usr/bin/env bash
# ¿Lo que corre en prod es lo que dice master? Corre una vez por día.
#
# El caso que ataja: master avanzó, el deploy no llegó (hook apagado, pipeline
# en rojo, alguien deployó a mano y se olvidó) y nadie se entera hasta que un
# bug "ya arreglado" aparece en producción.
set -euo pipefail

MIRROR=${ONNIX_MIRROR:-/srv/onnix/repo.git}
CONTAINER=${ONNIX_CONTAINER:-onnix-panel}

esperado=$(git -C "$MIRROR" rev-parse refs/heads/master)
corriendo=$(docker inspect "$CONTAINER" \
    --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true)

if [ -z "$corriendo" ]; then
    echo "el contenedor $CONTAINER no lleva label de revisión — imagen buildeada sin GIT_SHA"
    exit 1
fi
if [ "$corriendo" != "$esperado" ]; then
    echo "DRIFT: prod corre ${corriendo:0:12} y master está en ${esperado:0:12}"
    git -C "$MIRROR" log --oneline "$corriendo..$esperado" | head -10 || true
    exit 1
fi

# El pipeline se instala a mano y corre desde /usr/local/bin: un cambio a
# pipeline.sh puede estar en master, desplegado, y no aplicarse. Eso también es
# drift, y no lo dice el label de la imagen.
INSTALADO=${ONNIX_PIPELINE_BIN:-/usr/local/bin/onnix-pipeline}
EN_ARBOL=${ONNIX_PROD:-/srv/onnix/prod}/scripts/ci/pipeline.sh
if [ -f "$EN_ARBOL" ] && ! cmp -s "$INSTALADO" "$EN_ARBOL"; then
    echo "DRIFT: $INSTALADO no es el pipeline.sh de este commit — corré: sudo ${ONNIX_PROD:-/srv/onnix/prod}/scripts/ci/install.sh"
    exit 1
fi

echo "prod al día en ${corriendo:0:12}"

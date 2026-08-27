#!/usr/bin/env bash
# Renderiza los tutoriales y los deja con el nombre que el panel espera.
#
# El nombre del MP4 se deriva del `id` de la composición en kebab-case:
#   ContestarUnaConversacion -> contestar-una-conversacion.mp4
#
# Esa transformación está escrita en dos lados a propósito: acá, que es quien
# nombra el archivo, y en `slug_de()` de `panel/app/routes/tutoriales.py`, que
# es quien arma la URL. `panel/tests/test_tutoriales.py` compara las dos.
#
# Uso:
#   scripts/render_tutoriales.sh              # captura + renderiza los cinco
#   scripts/render_tutoriales.sh --sin-captura   # sólo renderiza
#
# Después:
#   rsync -avz tutoriales/video/out/ onnix:/home/onnix/tutoriales/
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VIDEO="$RAIZ/tutoriales/video"

cd "$VIDEO"

if [[ "${1:-}" != "--sin-captura" ]]; then
    echo "==> Capturando las pantallas del panel"
    npm run capturar
fi

# Los ids salen del guion, que es la fuente. Una segunda lista acá sería la
# forma más fácil de renderizar cuatro videos cuando hay cinco.
#
# `[A-Za-z0-9]` y no `[A-Za-z]`: con la clase sin dígitos, un id como
# `VerElCRM2` no matchea y el script renderiza uno menos SIN DECIR NADA. El
# guard de abajo cubre el cero; este cubre el «uno menos», que es el que no se
# nota.
#
# Se cuenta SÓLO desde `export const GUIONES` para abajo, igual que el parser de
# `panel/tests/test_tutoriales.py`. Arriba, el `type Guion` declara `id`,
# `titulo` y `promesa` con los mismos nombres: contar el archivo entero da un
# guion fantasma. Es la trampa que este repo ya tiene escrita —el que declara la
# regla nombra lo que la regla busca— y la primera versión de este guard se la
# comió: contaba 6 promesas contra 5 ids y abortaba sobre un archivo sano.
GUIONES=$(sed -n '/export const GUIONES/,$p' src/tutorial/guion.ts)

IDS=$(echo "$GUIONES" | grep -oE '^\s+id: "[A-Za-z0-9]+"' | sed -E 's/.*"(.*)"/\1/')

if [[ -z "$IDS" ]]; then
    echo "ERROR: no se encontró ningún id en src/tutorial/guion.ts" >&2
    exit 1
fi

# Un id por guion. Si el grep se comió alguno, el conteo lo dice acá y no tres
# minutos después, con un MP4 de menos y el panel sirviendo un reproductor mudo.
CUANTOS_IDS=$(echo "$IDS" | wc -l | tr -d ' ')
CUANTAS_PROMESAS=$(echo "$GUIONES" | grep -cE '^\s+promesa:' | tr -d ' ')
if [[ "$CUANTOS_IDS" != "$CUANTAS_PROMESAS" ]]; then
    echo "ERROR: $CUANTOS_IDS ids contra $CUANTAS_PROMESAS guiones en src/tutorial/guion.ts." >&2
    echo "       El grep de ids se comió alguno — revisá el patrón antes de renderizar." >&2
    exit 1
fi

mkdir -p out

for ID in $IDS; do
    # CamelCase -> kebab-case. Igual que slug_de() en el panel.
    SLUG=$(echo "$ID" | sed -E 's/([a-z0-9])([A-Z])/\1-\2/g' | tr '[:upper:]' '[:lower:]')
    echo "==> $ID -> out/$SLUG.mp4"
    npx remotion render "$ID" "out/$SLUG.mp4"
done

echo
echo "Listos en $VIDEO/out:"
ls -la out/

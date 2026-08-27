#!/usr/bin/env bash
# Regenera la allowlist de nginx con los rangos desde los que GitHub manda
# webhooks. Corre semanalmente: esos rangos cambian, y hardcodearlos una vez
# significa que en unos meses los deploys dejan de llegar y nadie sabe por qué.
#
# La allowlist es defensa en profundidad. Quien autentica es el HMAC.
set -euo pipefail

DEST=${ONNIX_ALLOW_CONF:-/etc/nginx/snippets/github-hooks-allow.conf}
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

rangos=$(curl -fsS --max-time 20 https://api.github.com/meta \
    | python3 -c 'import json,sys; [print(c) for c in json.load(sys.stdin)["hooks"]]')

# Si la API contesta cualquier cosa, no se pisa una allowlist que funciona.
[ "$(printf '%s\n' "$rangos" | grep -c '/')" -ge 3 ] \
    || { echo "api.github.com/meta devolvió algo inesperado — no toco $DEST" >&2; exit 1; }

{
    echo "# Generado por onnix-github-ips.service — no editar a mano."
    echo "# Fuente: https://api.github.com/meta (.hooks) — $(date -Is)"
    printf '%s\n' "$rangos" | sed 's/^/allow /; s/$/;/'
    echo "deny all;"
} > "$tmp"

if ! cmp -s "$tmp" "$DEST"; then
    install -o root -g root -m 644 "$tmp" "$DEST"
    nginx -t && systemctl reload nginx
    echo "allowlist actualizada"
else
    echo "allowlist sin cambios"
fi

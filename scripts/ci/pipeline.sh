#!/usr/bin/env bash
# Pipeline de CI/CD de Onnix. Lo dispara onnix-pipeline.path cuando el receptor de
# webhooks deja un job en la cola. Corre como onnix, que tiene docker y por
# lo tanto es root efectivo: la frontera de seguridad es el HMAC del receptor y
# la validación del SHA contra el mirror, no los permisos de este proceso.
#
# Se instala en /usr/local/bin/onnix-pipeline (root:root 755) con
# scripts/ci/install.sh — a propósito NO se ejecuta desde el worktree que el
# propio pipeline actualiza.
set -euo pipefail

QUEUE=${ONNIX_QUEUE:-/var/lib/onnixci/queue}
MIRROR=${ONNIX_MIRROR:-/srv/onnix/repo.git}
WORK=${ONNIX_WORK:-/srv/onnix/work}       # donde corre la suite (.env = credenciales dummy)
STAGE=${ONNIX_STAGE:-/srv/onnix/stage}    # desde donde se levanta staging (.env real)
PROD=${ONNIX_PROD:-/srv/onnix/prod}       # desde donde se levanta prod (.env real + landing)
LOCK=${ONNIX_SUITE_LOCK:-/var/lock/onnix-suite.lock}
LOCK_WAIT=${ONNIX_SUITE_LOCK_WAIT:-1800}
STATUS=${ONNIX_STATUS:-/var/lib/onnixci/status.json}
LOGDIR=${ONNIX_LOGDIR:-/var/lib/onnixci/logs}
PROJECT=onnix
KEEP_TAGS=${ONNIX_KEEP_TAGS:-5}

export ONNIX_STATUS="$STATUS"
mkdir -p "$LOGDIR"

log()  { printf '%s | %s\n' "$(date -Is)" "$*"; }

# Deja $1 exactamente en el commit $2, o falla ruidoso. $3 es cómo nombrarlo.
#
# `--force` porque un archivo trackeado y modificado —un tailwind.css compilado
# a mano, por ejemplo— aborta el checkout, y `clean -fd` no lo borra: solo toca
# lo no trackeado. El assert final es el que convierte "no pude" en un job que
# se detiene, en vez de uno que sigue con el árbol viejo.
checkout_o_morir() {
    local dir=$1 sha=$2 nombre=$3 head_real
    git -C "$dir" fetch --quiet origin \
        || { log "no pude fetchear $nombre ($dir)"; return 1; }
    git -C "$dir" checkout --quiet --detach --force "$sha" \
        || { log "no pude checkoutear $sha en $nombre ($dir)"; return 1; }
    git -C "$dir" clean -qfd -e .env -e logs
    head_real=$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)
    [ "$head_real" = "$sha" ] \
        || { log "$nombre quedó en ${head_real:-nada} y no en $sha"; return 1; }
}
fail() { log "ROJO: $*"; return 1; }

# ---------------------------------------------------------------------------
# status.json — lo último que pasó, legible sin journalctl
# ---------------------------------------------------------------------------
write_status() {
    local branch=$1 sha=$2 actor=$3 delivery=$4 result=$5 detail=$6 started=$7
    local tmp
    # Fail-loud pero no fatal: si no se puede escribir el estado, el deploy ya
    # pasó y no tiene sentido tumbarlo — pero tiene que verse en el log, porque
    # el gate de producción lee este archivo y sin él prod nunca deploya.
    tmp=$(mktemp "${STATUS}.XXXXXX") || { log "no pude crear el temporal de status.json"; return 0; }
    python3 - "$tmp" "$branch" "$sha" "$actor" "$delivery" "$result" "$detail" "$started" <<'PY'
import json, sys, time, os
tmp, branch, sha, actor, delivery, result, detail, started = sys.argv[1:9]
prev = {}
path = os.environ.get("ONNIX_STATUS", "/var/lib/onnixci/status.json")
try:
    with open(path) as fh:
        prev = json.load(fh)
except Exception:
    prev = {}
entry = {
    "branch": branch, "sha": sha, "actor": actor, "delivery": delivery,
    "result": result, "detail": detail,
    "started_at": started,
    "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "duration_s": int(time.time() - float(started or time.time())),
}
history = prev.get("history", [])
history.insert(0, entry)
out = {"last": entry, "last_por_rama": prev.get("last_por_rama", {}), "history": history[:50]}
out["last_por_rama"][branch] = entry
with open(tmp, "w") as fh:
    json.dump(out, fh, indent=2, ensure_ascii=False)
PY
    if [ ! -s "$tmp" ]; then
        log "status.json quedó vacío — no lo piso"
        rm -f "$tmp"
        return 0
    fi
    mv -f "$tmp" "$STATUS"
    chmod 644 "$STATUS"
}

# ---------------------------------------------------------------------------
# ¿Hay un staging verde para este commit? (la regla del CLAUDE.md, como un if)
# ---------------------------------------------------------------------------
staging_verde_para() {
    local sha=$1 staging_sha
    staging_sha=$(python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
e = d.get("last_por_rama", {}).get("dev") or {}
print(e.get("sha", "") if e.get("result") == "verde" else "")
' "$STATUS" 2>/dev/null || true)
    [ -n "$staging_sha" ] || return 1
    git -C "$MIRROR" merge-base --is-ancestor "$staging_sha" "$sha"
}

# ---------------------------------------------------------------------------
# Un job
# ---------------------------------------------------------------------------
run_job() {
    local job=$1 started; started=$(date +%s)
    local sha branch actor delivery
    sha=$(sed -n 's/^sha=\([0-9a-f]\{40\}\)$/\1/p'      "$job" | head -1)
    branch=$(sed -n 's/^branch=\(dev\|master\)$/\1/p'   "$job" | head -1)
    actor=$(sed -n 's/^actor=\([A-Za-z0-9._-]*\)$/\1/p' "$job" | head -1)
    delivery=$(sed -n 's/^delivery=\([A-Za-z0-9-]*\)$/\1/p' "$job" | head -1)

    [ -n "$sha" ] && [ -n "$branch" ] || { log "job ilegible, descartado"; return 1; }
    log "=== job branch=$branch sha=$sha actor=${actor:-?} delivery=${delivery:-?} ==="

    local sha12=${sha:0:12} deploy_dir compose_files service image container port
    if [ "$branch" = dev ]; then
        deploy_dir=$STAGE; compose_files=(-f docker-compose.dev.yml)
        service=panel-dev; image=onnix-panel-dev; container=onnix-panel-dev; port=8001
    else
        deploy_dir=$PROD;  compose_files=()
        service=panel;     image=onnix-panel;     container=onnix-panel;     port=8000
    fi

    # --- 1. traer el código y validar que el SHA existe de verdad -----------
    # rc=75 (EX_TEMPFAIL) = "no pude ni empezar": el job vuelve a la cola y el
    # timer lo reintenta. Distinto de rojo, que significa "este commit está mal"
    # y por lo tanto reintentarlo no arregla nada.
    #
    # El fetch completo se cae entero si CUALQUIER worktree del mirror tiene una
    # rama checkouteada —git se niega a mover un ref que alguien tiene puesto— y
    # entonces un worktree ajeno bloquea todos los deploys. Pasó el 2026-08-18
    # con `fix/carril-g-crm` en /srv/onnix/wt-carrilg. Si el completo falla, se
    # intenta el único ref que este job necesita.
    mirror_fetch() { sudo -u onnixci -H env HOME=/var/lib/onnixci git -C "$MIRROR" "$@"; }
    mirror_fetch fetch --prune --quiet origin \
        || { log "el fetch completo del mirror falló; pruebo sólo refs/heads/$branch"
             mirror_fetch fetch --quiet origin "+refs/heads/$branch:refs/heads/$branch"; } \
        || { write_status "$branch" "$sha" "$actor" "$delivery" rojo "fetch del mirror falló" "$started"; return 75; }
    git -C "$MIRROR" cat-file -e "${sha}^{commit}" 2>/dev/null \
        || { log "el SHA no existe en el mirror"; write_status "$branch" "$sha" "$actor" "$delivery" rojo "sha inexistente en el mirror" "$started"; return 1; }
    git -C "$MIRROR" merge-base --is-ancestor "$sha" "refs/heads/$branch" \
        || { log "el SHA no es ancestro de $branch"; write_status "$branch" "$sha" "$actor" "$delivery" rojo "sha no pertenece a $branch" "$started"; return 1; }

    # --- 2. gate de producción: staging verde en este mismo commit ----------
    if [ "$branch" = master ] && ! staging_verde_para "$sha"; then
        log "no hay un staging verde que sea ancestro de $sha12 — prod no se toca"
        write_status master "$sha" "$actor" "$delivery" rojo "sin staging verde previo" "$started"
        return 1
    fi

    # --- 3. checkout del worktree de test ----------------------------------
    # Los tres comandos de acá abajo NO pueden fallar en silencio. Sin el
    # `|| return 75` y sin el assert del final, un checkout que falla deja el
    # worktree donde estaba: la suite corre el commit VIEJO y el build de abajo
    # construye ese mismo código con el SHA nuevo en el label. Verde mentiroso y
    # deploy fantasma, los dos a la vez.
    #
    # Pasó del 18/08 23:44 al 20/08: un `tailwind.css` modificado en el worktree
    # bloqueaba todos los checkouts —`clean -fd` no toca archivos trackeados
    # modificados, y encima corre después— y los dos ambientes quedaron con la
    # imagen de 72e86f2 etiquetada con el SHA que se pidiera. `/health` decía
    # b803db7 y adentro no estaban ni money.py ni crm_followup.html.
    checkout_o_morir "$WORK" "$sha" "el worktree de test" \
        || { write_status "$branch" "$sha" "$actor" "$delivery" rojo \
                "no pude dejar el worktree de test en el SHA pedido" "$started"; return 75; }

    # --- 4. build, en paralelo con los tests (fuera del lock) ---------------
    local buildlog=$LOGDIR/build-$sha12.log
    ( cd "$WORK" && GIT_SHA=$sha docker compose -p "$PROJECT" "${compose_files[@]}" \
        build "$service" ) > "$buildlog" 2>&1 &
    local build_pid=$!
    log "build lanzado (pid $build_pid, log $buildlog)"

    # --- 5. la suite, bajo el mismo lock que usa un humano -----------------
    local suitelog=$LOGDIR/suite-$sha12.log suite_rc=0
    log "esperando el lock de la suite si hace falta ($LOCK)"
    ONNIX_SUITE_LOCK_WAIT=$LOCK_WAIT "$WORK/scripts/run_suite.sh" > "$suitelog" 2>&1 || suite_rc=$?
    log "suite rc=$suite_rc — $(tail -1 "$suitelog")"

    local build_rc=0
    wait "$build_pid" || build_rc=$?
    log "build rc=$build_rc"

    if [ "$suite_rc" -eq 75 ]; then
        # run_suite.sh devuelve 75 cuando se le acabó la espera del lock. El
        # commit no tiene la culpa: vuelve a la cola.
        write_status "$branch" "$sha" "$actor" "$delivery" rojo "no conseguí el lock de la suite en ${LOCK_WAIT}s" "$started"
        return 75
    fi
    if [ "$suite_rc" -ne 0 ]; then
        write_status "$branch" "$sha" "$actor" "$delivery" rojo "suite en rojo ($(tail -1 "$suitelog"))" "$started"
        return 1
    fi
    if [ "$build_rc" -ne 0 ]; then
        write_status "$branch" "$sha" "$actor" "$delivery" rojo "build falló ($(tail -3 "$buildlog" | tr '\n' ' '))" "$started"
        return 1
    fi

    # --- 6. identidad de la imagen -----------------------------------------
    local built_rev
    built_rev=$(docker image inspect "$image:latest" \
        --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true)
    if [ "$built_rev" != "$sha" ]; then
        log "la imagen quedó con revision='${built_rev:-<vacío>}' y se esperaba $sha"
        write_status "$branch" "$sha" "$actor" "$delivery" rojo "la imagen no lleva su revisión" "$started"
        return 1
    fi

    local anterior
    anterior=$(docker inspect "$container" \
        --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true)
    docker tag "$image:latest" "$image:$sha12"
    if [ -n "$anterior" ] && [ "$anterior" != "$sha" ]; then
        docker tag "$image:${anterior:0:12}" "$image:previous" 2>/dev/null || true
    fi

    # --- 7. ¿trae migración? entonces el rollback por tag no alcanza --------
    local aviso_alembic="" aviso_pipeline=""
    if [ -n "$anterior" ] && git -C "$MIRROR" cat-file -e "${anterior}^{commit}" 2>/dev/null; then
        if [ -n "$(git -C "$MIRROR" diff --name-only "$anterior" "$sha" -- panel/alembic/versions/)" ]; then
            aviso_alembic="ATENCIÓN: trae revisión de Alembic nueva — volver la imagen atrás NO vuelve el schema atrás"
            log "$aviso_alembic"
        fi
    fi

    # --- 8. deploy ----------------------------------------------------------
    checkout_o_morir "$deploy_dir" "$sha" "el árbol de deploy" \
        || { write_status "$branch" "$sha" "$actor" "$delivery" rojo \
                "no pude dejar $deploy_dir en el SHA pedido" "$started"; return 75; }

    # Las destacadas de la landing se generan acá y no en cada request: nginx
    # sirve el archivo del disco, así que la home sigue arriba aunque el panel
    # esté caído. Solo en prod, que es de donde se sirve la landing.
    #
    # Reescribe landing/index.html, que es un archivo trackeado. Eso antes
    # habría roto todos los deploys siguientes —el checkout se negaba a pisar un
    # archivo modificado—, y por eso esto no estaba enganchado. Ya no: el
    # checkout de arriba es `--force`.
    #
    # El script no toca el archivo y sale 0 si la base no contesta o si no hay
    # ninguna propiedad publicable, así que un deploy nunca se cae por esto; el
    # bloque commiteado es un estado vacío válido. Igual se loguea.
    # El `?v=` de los assets de la landing lo pone el deploy, no una persona.
    # nginx los sirve con `expires` de 30 dias: con el numero escrito a mano, un
    # cambio de CSS se despliega y no llega al que ya visito el sitio. Paso el
    # 2026-08-20 con el hero del celular.
    if [ "$branch" = master ] && [ -f "$deploy_dir/scripts/stamp_landing_assets.py" ]; then
        python3 "$deploy_dir/scripts/stamp_landing_assets.py" \
            --html "$deploy_dir/landing/index.html" --version "$sha12" >>"$buildlog" 2>&1 \
            || log "sello de assets: falló — la landing queda con el ?v= anterior"
    fi

    if [ "$branch" = master ] && [ -f "$deploy_dir/scripts/build_destacadas.py" ]; then
        if python3 "$deploy_dir/scripts/build_destacadas.py" \
                --html "$deploy_dir/landing/index.html" \
                --database onnix_prod >>"$buildlog" 2>&1; then
            log "destacadas de la landing regeneradas"
        else
            log "destacadas: no se pudieron regenerar — queda el bloque anterior"
        fi
    fi
    ( cd "$deploy_dir" && GIT_SHA=$sha ONNIX_IMAGE_TAG=$sha12 \
        docker compose -p "$PROJECT" "${compose_files[@]}" up -d --no-deps "$service" ) \
        >> "$buildlog" 2>&1 \
        || { write_status "$branch" "$sha" "$actor" "$delivery" rojo "docker compose up falló" "$started"; return 1; }

    # El pipeline se instala a mano (`scripts/ci/install.sh`) y corre desde
    # /usr/local/bin, a proposito: un script que se reescribe a si mismo
    # mientras corre es una forma barata de perder una tarde. El costo es que un
    # cambio a pipeline.sh se mergea, se deploya y NO se aplica, sin que nada lo
    # diga. Paso dos veces el 2026-08-20: el hook de destacadas y el sello de
    # assets viajaron a produccion y no corrieron. Ahora el deploy lo avisa.
    if [ "$branch" = master ] && [ -f "$deploy_dir/scripts/ci/pipeline.sh" ] \
       && ! cmp -s /usr/local/bin/onnix-pipeline "$deploy_dir/scripts/ci/pipeline.sh"; then
        log "ATENCION: el pipeline instalado no es el de este commit — corre: sudo $deploy_dir/scripts/ci/install.sh"
        aviso_pipeline=" · el pipeline instalado quedo viejo, corre install.sh"
    fi

    # --- 9. smoke -----------------------------------------------------------
    local ok=1 rev="" code=""
    for _ in $(seq 1 30); do
        rev=$(curl -fsS --max-time 5 "127.0.0.1:$port/health" 2>/dev/null \
              | python3 -c 'import json,sys; print(json.load(sys.stdin).get("revision",""))' 2>/dev/null || true)
        [ "$rev" = "$sha" ] && { ok=0; break; }
        sleep 2
    done
    if [ "$ok" -ne 0 ]; then
        log "el smoke no vio revision=$sha en /health (vio '${rev:-nada}')"
    else
        code=$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 5 "127.0.0.1:$port/login" || true)
        [ "$code" = "200" ] || { ok=1; log "/login devolvió ${code:-nada}"; }
    fi

    if [ "$ok" -ne 0 ]; then
        if [ -n "$anterior" ] && docker image inspect "$image:previous" >/dev/null 2>&1; then
            log "rollback automático a $image:previous"
            ( cd "$deploy_dir" && ONNIX_IMAGE_TAG=previous \
                docker compose -p "$PROJECT" "${compose_files[@]}" up -d --no-deps "$service" ) >> "$buildlog" 2>&1 || true
        fi
        write_status "$branch" "$sha" "$actor" "$delivery" rojo "smoke falló, se revirtió a la imagen anterior" "$started"
        return 1
    fi

    # --- 10. poda de tags viejas -------------------------------------------
    docker images --format '{{.Repository}}:{{.Tag}}' "$image" \
        | grep -vE ':(latest|previous)$' \
        | tail -n "+$((KEEP_TAGS + 1))" \
        | xargs -r -n1 docker rmi >/dev/null 2>&1 || true

    log "VERDE — $branch $sha12 desplegado en :$port ${aviso_alembic:+| $aviso_alembic}${aviso_pipeline}"
    write_status "$branch" "$sha" "$actor" "$delivery" verde "${aviso_alembic:-ok}${aviso_pipeline}" "$started"
}

# ---------------------------------------------------------------------------
# Barrido de huérfanos: un .running que sobrevivió a la corrida que lo tomó
#
# systemd serializa este service (Type=oneshot + el .path encolan, no paralelizan),
# así que cualquier .running que exista cuando arrancamos es de una corrida que
# murió sin terminarlo — SIGKILL, OOM, reboot, o el TimeoutStartSec de 70 min.
# No hace falta un umbral de antigüedad: no puede haber una corrida viva. Y si
# mientras tanto llegó un job nuevo de esa rama, gana el nuevo.
# ---------------------------------------------------------------------------
for b in master dev; do
    [ -f "$QUEUE/$b.running" ] || continue
    if [ -f "$QUEUE/$b.job" ]; then
        log "descarto $b.running: la corrida que lo tomó murió y ya hay un job más nuevo"
        rm -f "$QUEUE/$b.running"
    else
        log "recupero $b.running: la corrida que lo tomó murió sin terminarlo"
        mv "$QUEUE/$b.running" "$QUEUE/$b.job"
    fi
done

# ---------------------------------------------------------------------------
# Drenado: master antes que dev; un job rojo no frena al siguiente
#
# El job no se borra al empezar: se renombra a .running y recién se descarta
# cuando terminó de verdad. Antes se borraba primero, y entonces todo lo que
# matara al proceso —o cualquier fallo de infraestructura— hacía desaparecer el
# deploy sin dejar nada que reintentar. El 2026-08-18 el fetch del mirror falló
# por permisos, el job se marcó rojo y se descartó, y staging quedó tres horas
# atrás sin que nada avisara.
#
# El coalescing lo sigue dando este while: al terminar un job vuelve a mirar la
# cola, así que un push que llegó durante la corrida entra en la misma pasada.
# Lo que ya no lo da es el .path unit (con un .running adentro el directorio
# nunca queda vacío, y su disparo es la transición vacío→no-vacío); para esa
# ventana está el timer de 10 min, que es para lo que se puso.
#
# El `return` de abajo es para que un test pueda sourcear este archivo y llamar
# a una función suelta sin disparar la cola.
# ---------------------------------------------------------------------------
[ "${BASH_SOURCE[0]}" = "$0" ] || return 0

while :; do
    rama=""
    for b in master dev; do
        [ -f "$QUEUE/$b.job" ] && { rama=$b; break; }
    done
    [ -n "$rama" ] || break

    running=$QUEUE/$rama.running
    mv "$QUEUE/$rama.job" "$running"
    rc=0
    run_job "$running" || rc=$?

    if [ "$rc" -eq 75 ]; then
        # No pude ni empezar. El commit no tiene la culpa, así que vuelve a la
        # cola en vez de descartarse — salvo que ya haya llegado uno más nuevo.
        if [ -f "$QUEUE/$rama.job" ]; then
            log "no pude empezar el job de $rama, pero ya hay uno más nuevo: descarto el viejo"
            rm -f "$running"
        else
            mv "$running" "$QUEUE/$rama.job"
            log "no pude empezar el job de $rama — queda en la cola, el timer reintenta en <=10 min"
            break
        fi
    else
        rm -f "$running"
        [ "$rc" -eq 0 ] || log "el job terminó en rojo, sigo con la cola"
    fi
done

log "cola vacía"

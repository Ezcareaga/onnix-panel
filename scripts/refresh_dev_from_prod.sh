#!/usr/bin/env bash
# =============================================================================
# Onnix SA — Refresh staging DB from production (STAB-09 / TD-115-03)
#
# Canonical, repeatable procedure to refresh the STAGING database
# (onnix_dev / dev.onnix.com.py) from a fresh PRODUCTION snapshot
# (onnix_prod). Replaces the bare CLAUDE.md one-liner pg_dump with the full
# 5-step procedure that also flushes the staging app pool, terminates lingering
# idle-in-transaction backends, brings dev's alembic to head, and asserts the
# contacts attnum is back near the prod baseline (bloat residue gone).
#
# THIS OVERWRITES onnix_dev FROM onnix_prod.
#   SOURCE = onnix_prod   (READ-ONLY here — only ever read via pg_dump)
#   TARGET = onnix_dev     (DROP/CREATE'd by --clean restore)
#
# Hard safety rails:
#   - Refuses to run if TARGET is onnix_prod (never write to prod).
#   - Requires --yes (or REFRESH_CONFIRM=yes) — cannot run accidentally.
#   - Never connects staging to prod; the dump streams over a local docker pipe.
#
# 5 steps (LOCKED — see 118-01-SUMMARY.md / 119-09-PLAN.md):
#   1. restart onnix-panel-dev          (flush the staging app connection pool)
#   2. terminate idle-in-tx backends  (usename='onnix' filter — OQ-3,
#                                       NEVER application_name; panel app_name is EMPTY)
#   3. RESET public schema in dev (DROP SCHEMA public CASCADE; recreate + prod's
#      public extensions) THEN pg_dump --exclude-schema=n8n --clean --if-exists
#      --no-owner prod | psql dev WITH ON_ERROR_STOP=1
#   4. alembic upgrade head           (prod snapshot may lag the latest migration)
#   5. attnum assert (≈ baseline ~9) + faithful-snapshot PARITY asserts vs prod
#      (row counts, users id-set, FK-to-users constraint set) + /login 200
#
# STAB-09 first-run bug (fixed in this version):
#   The --clean restore alone emits DROPs only for objects present in PROD, so
#   target-only LEGACY objects (e.g. contacts_assigned_to_fkey referencing users)
#   blocked DROP of users → users was never replaced (stale dev table, missing the
#   real prod agent id=1109), COPY/PK/FK failed silently because the restore ran
#   with ON_ERROR_STOP=0 and the shallow smoke passed anyway. Fix = reset the dev
#   public schema BEFORE the restore (kills any target-only legacy object) + run
#   the restore with ON_ERROR_STOP=1 + replace the shallow smoke with prod-vs-dev
#   parity asserts. Dropping ONLY 'public' leaves dev's 'n8n' schema untouched.
#
# USAGE
#   bash scripts/refresh_dev_from_prod.sh --yes      # run the refresh
#   bash scripts/refresh_dev_from_prod.sh            # prints what it would do, then refuses
#
# Exit 0 = refresh complete + asserts passed. Non-zero = refused or a step/assert failed.
# =============================================================================

set -euo pipefail

# ---------------------------
# Configuration (overridable for safety/testing; defaults target STAGING ONLY)
# ---------------------------
PG_CONTAINER="${PG_CONTAINER:-onnix-postgres}"
PANEL_CONTAINER="${PANEL_CONTAINER:-onnix-panel-dev}"   # staging panel — NEVER onnix-panel (prod)
DB_USER="${DB_USER:-onnix}"
SOURCE_DB="${SOURCE_DB:-onnix_prod}"              # read-only via pg_dump
TARGET_DB="${TARGET_DB:-onnix_dev}"               # overwritten by --clean restore
PANEL_DIR="${PANEL_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../panel" && pwd)}"
DEV_LOGIN_URL="${DEV_LOGIN_URL:-http://localhost:8001/login}"

# attnum baseline: prod contacts has ~9 columns. Post-refresh dev should match
# (NOT ~552 — that bloat is migration-roundtrip residue, the very thing this clears).
ATTNUM_MAX_OK="${ATTNUM_MAX_OK:-50}"                  # generous ceiling vs the ~552 bloat

UAT_EMAIL="agent_uat@onnix.com.py"            # must be ABSENT after a prod refresh

# ---------------------------
# Args
# ---------------------------
CONFIRM="${REFRESH_CONFIRM:-no}"
for arg in "$@"; do
    case "$arg" in
        --yes) CONFIRM="yes" ;;
        -h|--help)
            grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: ${arg} (use --yes to run, -h for help)" >&2
            exit 2
            ;;
    esac
done

# ---------------------------
# Safety rails
# ---------------------------
say()  { echo "[refresh_dev_from_prod] $*"; }
die()  { echo "[refresh_dev_from_prod] ERROR: $*" >&2; exit 1; }

# RAIL 1 — never write to prod. The TARGET (the DB we DROP/CREATE) must NOT be prod.
if [ "${TARGET_DB}" = "onnix_prod" ] || [ "${TARGET_DB}" = "${SOURCE_DB}" ]; then
    die "refusing to run — TARGET_DB='${TARGET_DB}' would overwrite production (SOURCE='${SOURCE_DB}'). This script ONLY refreshes staging."
fi
# RAIL 1b — the panel container we restart must be the staging one, never prod.
if [ "${PANEL_CONTAINER}" = "onnix-panel" ]; then
    die "refusing to run — PANEL_CONTAINER='onnix-panel' is PRODUCTION. This script ONLY touches staging (onnix-panel-dev)."
fi

say "SOURCE (read-only) = ${SOURCE_DB}"
say "TARGET (OVERWRITTEN) = ${TARGET_DB}"
echo
echo "  ╔══════════════════════════════════════════════════════════════════╗"
echo "  ║  THIS OVERWRITES ${TARGET_DB} FROM ${SOURCE_DB}."
echo "  ║  All current staging data is destroyed and replaced with a fresh   "
echo "  ║  production snapshot (dev.onnix.com.py staging DB).                 "
echo "  ╚══════════════════════════════════════════════════════════════════╝"
echo

# RAIL 2 — explicit confirmation required.
if [ "${CONFIRM}" != "yes" ]; then
    say "DRY-RUN: no destructive action taken. Re-run with --yes to actually refresh ${TARGET_DB}."
    say "Steps that WOULD run:"
    say "  1. docker restart ${PANEL_CONTAINER}"
    say "  2. terminate idle-in-tx backends on ${TARGET_DB} (usename='${DB_USER}')"
    say "  3a. RESET ${TARGET_DB} public schema (DROP SCHEMA public CASCADE; recreate + prod's public extensions) — n8n schema untouched"
    say "  3b. pg_dump --exclude-schema=n8n --clean --if-exists --no-owner ${SOURCE_DB} | psql ${TARGET_DB} (ON_ERROR_STOP=1)"
    say "  4. (cd ${PANEL_DIR} && POSTGRES_HOST=127.0.0.1 POSTGRES_DB=${TARGET_DB} alembic upgrade head)"
    say "  5. assert contacts attnum ≈ baseline (≤ ${ATTNUM_MAX_OK}) + PARITY asserts vs ${SOURCE_DB} (row counts, users id-set, FK-to-users set) + ${UAT_EMAIL} absent + /login 200"
    exit 0
fi

# Verify the postgres container is up before we start mutating.
docker inspect -f '{{.State.Running}}' "${PG_CONTAINER}" 2>/dev/null | grep -q true \
    || die "postgres container '${PG_CONTAINER}' is not running."

# Convenience wrappers — psql against the TARGET (never prod).
psql_dev()  { docker exec "${PG_CONTAINER}" psql -U "${DB_USER}" -d "${TARGET_DB}" "$@"; }
psql_devq() { docker exec "${PG_CONTAINER}" psql -U "${DB_USER}" -d "${TARGET_DB}" -tA -c "$1"; }
# READ-ONLY wrapper against the SOURCE (prod) — used ONLY for parity asserts in
# STEP 5. Single -c SELECT statements only; never mutates prod.
psql_prodq() { docker exec "${PG_CONTAINER}" psql -U "${DB_USER}" -d "${SOURCE_DB}" -tA -c "$1"; }

# ---------------------------
# Step 1 — flush the staging app pool
# ---------------------------
say "STEP 1/5 — restart ${PANEL_CONTAINER} (flush staging connection pool)"
# Default: docker restart onnix-panel-dev  (PANEL_CONTAINER override above; NEVER onnix-panel/prod)
docker restart "${PANEL_CONTAINER}" >/dev/null \
    || die "failed to restart ${PANEL_CONTAINER}"

# ---------------------------
# Step 2 — terminate lingering idle-in-transaction backends (OQ-3: usename, NOT application_name)
# ---------------------------
say "STEP 2/5 — terminate idle-in-transaction backends on ${TARGET_DB} (usename='${DB_USER}')"
psql_dev -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle in transaction' AND usename='${DB_USER}';" \
    || die "failed to terminate idle-in-tx backends"

# Also terminate ALL non-self backends on the TARGET so the --clean DROP/CREATE
# below is not blocked by an open session. Filtered to the TARGET db + this user,
# excluding our own pid; prod (other db) is untouched.
say "STEP 2/5 — terminate remaining non-self backends on ${TARGET_DB} (unblock --clean restore)"
psql_dev -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${TARGET_DB}' AND usename='${DB_USER}' AND pid <> pg_backend_pid();" \
    || say "WARN: non-self backend terminate returned non-zero (continuing; restore may still succeed)"

# ---------------------------
# Step 3 — reset dev's public schema, then dump prod (excluding n8n) and restore
# ---------------------------
# 3a. RESET the TARGET public schema BEFORE the restore.
#     WHY (STAB-09 first-run bug): `pg_dump --clean` emits DROP statements ONLY
#     for objects that exist in the SOURCE (prod). Any TARGET-ONLY legacy object
#     (e.g. contacts_assigned_to_fkey / lead_events_assigned_to_fkey referencing
#     users — present in dev, absent in prod) is therefore NEVER dropped and can
#     block `DROP TABLE users` / `DROP CONSTRAINT users_pkey`, leaving users stale.
#     Dropping the whole public schema first guarantees a clean slate so --clean
#     can never collide. We drop ONLY 'public' — dev's 'n8n' schema is untouched.
#     The prod public-schema extensions are recreated here so the dump's data
#     (which depends on e.g. vector/pg_trgm types) restores cleanly.
say "STEP 3/5 (a) — RESET ${TARGET_DB} public schema (DROP SCHEMA public CASCADE; recreate) — n8n schema untouched"
docker exec "${PG_CONTAINER}" psql -U "${DB_USER}" -d "${TARGET_DB}" -v ON_ERROR_STOP=1 -c "
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO ${DB_USER};
GRANT ALL ON SCHEMA public TO public;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\" WITH SCHEMA public;
" >/dev/null \
    || die "failed to reset ${TARGET_DB} public schema (pre-restore)"

# 3b. Restore the prod snapshot. ON_ERROR_STOP=1 so ANY error aborts the whole
#     script (set -e). The dump's `DROP ... IF EXISTS` statements are harmless
#     no-ops on the fresh schema. onnix is superuser, so CREATE/COMMENT ON
#     EXTENSION succeed (verified) — no line filtering required.
say "STEP 3/5 (b) — pg_dump ${SOURCE_DB} (--exclude-schema=n8n) | psql ${TARGET_DB} (ON_ERROR_STOP=1)"
if ! docker exec "${PG_CONTAINER}" pg_dump -U "${DB_USER}" --exclude-schema=n8n --clean --if-exists --no-owner "${SOURCE_DB}" \
    | docker exec -i "${PG_CONTAINER}" psql -U "${DB_USER}" -v ON_ERROR_STOP=1 -d "${TARGET_DB}" >/dev/null; then
    die "pg_dump | psql restore failed (ON_ERROR_STOP=1 aborted on first error)"
fi

# ---------------------------
# Step 4 — bring dev's alembic to head
# ---------------------------
say "STEP 4/5 — alembic upgrade head on ${TARGET_DB}"
if [ -d "${PANEL_DIR}" ]; then
    ( cd "${PANEL_DIR}" && POSTGRES_HOST=127.0.0.1 POSTGRES_DB="${TARGET_DB}" alembic upgrade head ) \
        || die "alembic upgrade head failed against ${TARGET_DB}"
else
    die "panel dir '${PANEL_DIR}' not found — cannot run alembic upgrade head"
fi

# ---------------------------
# Step 5 — attnum assert (≈ baseline) + smoke
# ---------------------------
say "STEP 5/5 — attnum assert + smoke"

ATTNUM=$(psql_devq "SELECT max(attnum) FROM pg_attribute a JOIN pg_class c ON a.attrelid=c.oid WHERE c.relname='contacts';" || true)
[ -n "${ATTNUM}" ] || die "could not read contacts attnum from ${TARGET_DB}"
if [ "${ATTNUM}" -gt "${ATTNUM_MAX_OK}" ]; then
    die "contacts attnum=${ATTNUM} still bloated (> ${ATTNUM_MAX_OK}). Expected ≈ prod baseline (~9). Refresh did NOT clear the bloat."
fi
say "  attnum OK: contacts max(attnum)=${ATTNUM} (≤ ${ATTNUM_MAX_OK}, near prod baseline ~9)"

# ---- Faithful-snapshot PARITY asserts (prod read-only vs dev) ----------------
# After a clean restore, dev MUST be byte-faithful to prod for these tables. The
# STAB-09 first-run bug left users stale (4 rows vs prod 5, missing the real prod
# agent id=1109) and dropped contacts_agent_user_id_fkey, yet the OLD shallow
# smoke (count==4, /login 200) passed. These asserts make any such drift FATAL.

# 1) Row-count equality for the snapshot-faithful tables.
PARITY_TABLES="users contacts lead_events messages conversations visits properties contact_notes auth_audit"
printf '  %-16s %12s %12s   %s\n' "table" "prod" "dev" "status"
PARITY_FAIL=0
for t in ${PARITY_TABLES}; do
    P=$(psql_prodq "SELECT count(*) FROM ${t};" || echo "ERR")
    D=$(psql_devq  "SELECT count(*) FROM ${t};" || echo "ERR")
    if [ "${P}" = "${D}" ] && [ "${P}" != "ERR" ]; then
        printf '  %-16s %12s %12s   %s\n' "${t}" "${P}" "${D}" "OK"
    else
        printf '  %-16s %12s %12s   %s\n' "${t}" "${P}" "${D}" "MISMATCH"
        PARITY_FAIL=1
    fi
done
[ "${PARITY_FAIL}" = "0" ] || die "row-count parity MISMATCH between ${SOURCE_DB} and ${TARGET_DB} (see table above)."
say "  row-count parity OK for: ${PARITY_TABLES}"

# 2) users id-set equality (catches the exact STAB-09 failure: missing id 1109).
USERS_IDS_PROD=$(psql_prodq "SELECT string_agg(id::text, ',' ORDER BY id) FROM users;")
USERS_IDS_DEV=$(psql_devq  "SELECT string_agg(id::text, ',' ORDER BY id) FROM users;")
if [ "${USERS_IDS_PROD}" != "${USERS_IDS_DEV}" ]; then
    die "users id-set MISMATCH — prod=[${USERS_IDS_PROD}] dev=[${USERS_IDS_DEV}] (stale dev users table — the STAB-09 bug)."
fi
USERS=$(psql_devq "SELECT count(*) FROM users;")
say "  users id-set parity OK: count=${USERS}, ids=[${USERS_IDS_DEV}]"

# 3) FK-to-users constraint-name set equality (catches missing contacts_agent_user_id_fkey).
FK_PROD=$(psql_prodq "SELECT string_agg(conname, ',' ORDER BY conname) FROM pg_constraint WHERE confrelid='users'::regclass;")
FK_DEV=$(psql_devq  "SELECT string_agg(conname, ',' ORDER BY conname) FROM pg_constraint WHERE confrelid='users'::regclass;")
if [ "${FK_PROD}" != "${FK_DEV}" ]; then
    die "FK-to-users constraint set MISMATCH — prod=[${FK_PROD}] dev=[${FK_DEV}] (the STAB-09 bug dropped contacts_agent_user_id_fkey)."
fi
say "  FK-to-users constraint parity OK: [${FK_DEV}]"

# 4) No orphan contacts (agent_user_id referencing an absent user). Belt-and-braces
#    on top of the FK existing — proves the data is internally consistent.
ORPHANS=$(psql_devq "SELECT count(*) FROM contacts c WHERE c.agent_user_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id=c.agent_user_id);")
if [ "${ORPHANS}" != "0" ]; then
    die "found ${ORPHANS} orphan contact(s) with agent_user_id not present in users (snapshot inconsistency)."
fi
say "  orphan contacts: 0 (agent_user_id integrity OK)"

# agent_uat must be ABSENT — prod has no UAT agent, so the snapshot recreates dev without it.
UAT_PRESENT=$(psql_devq "SELECT count(*) FROM users WHERE email='${UAT_EMAIL}';" || echo "0")
if [ "${UAT_PRESENT}" != "0" ]; then
    die "${UAT_EMAIL} is still present (${UAT_PRESENT}) — prod snapshot should not contain the UAT agent."
fi
say "  ${UAT_EMAIL} absent (as expected from a prod snapshot)"

# Optional HTTP smoke against staging (non-fatal — the DB asserts above are authoritative).
if command -v curl >/dev/null 2>&1; then
    CODE=$(curl -fsS -o /dev/null -w '%{http_code}' "${DEV_LOGIN_URL}" 2>/dev/null || echo "000")
    if [ "${CODE}" = "200" ]; then
        say "  staging /login smoke: HTTP ${CODE} (${DEV_LOGIN_URL})"
    else
        say "  WARN: staging /login smoke returned HTTP ${CODE} (${DEV_LOGIN_URL}) — container may still be starting after the STEP 1 restart"
    fi
fi

echo
say "REFRESH COMPLETE — dev attnum=${ATTNUM}, users=${USERS} (parity OK), FK-to-users + row-count parity GREEN, ${UAT_EMAIL}=absent"

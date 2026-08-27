#!/usr/bin/env bash
#
# check_container_parity.sh — STAB-06 / TD-116-04 / CLEAN-09 container parity gate.
#
# WHY THIS EXISTS
#   panel/Dockerfile COPYs tests/ into the image (CLEAN-09 close), so verify phases can
#   run pytest INSIDE the deployment artifact. But two host-collectable test files cannot
#   (or should not) collect in the panel container, which used to look like silent drift:
#
#     1. tests/test_scraper_ic_login_button.py
#        DOCUMENTED IGNORE. The scraper module imports `psycopg` + the `shared.db` package,
#        neither of which exists in the panel image. COPYing scrapers/ (8.1MB) would couple
#        the panel image to the scraper stack — out of scope. So this file is --ignored in
#        the container and counted as a boundary decision, NOT drift.
#
#     2. tests/test_twilio_templates_m3_script.py
#        RESOLVED VIA COPY. The test imports scripts/twilio_create_templates_m3.py (only
#        needs `httpx`, which IS in the image). The test resolves the script with
#        Path(__file__).resolve().parent.parent.parent / "scripts" → container ROOT
#        /scripts/twilio_create_templates_m3.py. Docker build context is ./panel, so a
#        build-context-reachable copy lives at panel/scripts/ and the Dockerfile COPYs it
#        to /scripts/. With that COPY in place this file COLLECTS in-container → true parity.
#        (Fallback if COPY ever proves fragile: --ignore it too and add 96 to `ignored`.)
#
# ACCEPTANCE FORMULA (replaces the ROADMAP literal gap==0; documented ignores are boundary
# decisions, not drift):
#
#     host_count - container_count - ignored_count == 0
#
#   HOST IS CANONICAL: the host runs all tests (official green). The container runs its
#   subset (everything except the documented --ignore'd file(s)). This gate asserts the
#   only difference between host and container is the documented ignore(s) — any other
#   delta is real, undocumented container drift and FAILS the gate (non-zero exit).
#
# USAGE
#   bash scripts/check_container_parity.sh
#   Exit 0 = PASS (parity holds). Exit 1 = FAIL (undocumented drift) or setup error.
#
# OVERRIDABLE ENV (defaults target staging):
#   CONTAINER   panel container name              (default: onnix-panel-dev — staging; NEVER prod)
#   PANEL_DIR   host path to panel/ (pytest cwd)  (default: /home/onnix/panel)
#   POSTGRES_HOST / POSTGRES_DB  host pytest DB   (default: 127.0.0.1 / onnix_dev)
#
set -euo pipefail

CONTAINER="${CONTAINER:-onnix-panel-dev}"
PANEL_DIR="${PANEL_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../panel" && pwd)}"
export POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
export POSTGRES_DB="${POSTGRES_DB:-onnix_dev}"

# Documented container ignore(s). The scraper file is always ignored (psycopg + shared.db
# missing). If the twilio COPY path is ever abandoned, add it here AND below in IGNORED_FILES.
SCRAPER_IGNORE="tests/test_scraper_ic_login_button.py"
IGNORE_ARGS="--ignore=${SCRAPER_IGNORE}"
# Files measured on the HOST to compute the exact `ignored` count (host is canonical).
IGNORED_FILES=("${SCRAPER_IGNORE}")

# ── helper: extract the trailing "collected N items" / "N tests collected" number ───────
_collected_count() {
    # reads pytest --collect-only -q output on stdin, prints the integer count
    grep -oE '[0-9]+ (tests|test) collected' | grep -oE '[0-9]+' | tail -1
}

echo "container parity gate (STAB-06 / TD-116-04 / CLEAN-09)"
echo "  container = ${CONTAINER}    panel dir = ${PANEL_DIR}"
echo

# ── HOST collection (canonical, full suite) ─────────────────────────────────────────────
host=$(cd "${PANEL_DIR}" && pytest --collect-only -q 2>/dev/null | _collected_count || true)
if [ -z "${host:-}" ]; then
    echo "FAIL: could not compute host collection count" >&2
    exit 1
fi

# ── CONTAINER collection (subset: documented --ignore for the scraper file) ─────────────
container=$(docker exec "${CONTAINER}" sh -c "cd /app && pytest --collect-only -q ${IGNORE_ARGS} 2>/dev/null" | _collected_count || true)
if [ -z "${container:-}" ]; then
    echo "FAIL: could not compute container collection count (is ${CONTAINER} running?)" >&2
    exit 1
fi

# ── IGNORED count: sum of the documented-ignored files, measured ON THE HOST ────────────
ignored=0
for f in "${IGNORED_FILES[@]}"; do
    n=$(cd "${PANEL_DIR}" && pytest --collect-only -q "${f}" 2>/dev/null | _collected_count || true)
    n="${n:-0}"
    echo "  ignored file: ${f} → ${n} test(s)"
    ignored=$(( ignored + n ))
done

echo
echo "  host       = ${host}"
echo "  container  = ${container}"
echo "  ignored    = ${ignored}"

delta=$(( host - container - ignored ))
echo "  host - container - ignored = ${delta}"
echo

if [ "${delta}" -eq 0 ]; then
    echo "PASS: container parity holds (host - container - ignored == 0)."
    exit 0
else
    echo "FAIL: undocumented container drift of ${delta} test(s)." >&2
    echo "      host(${host}) - container(${container}) - ignored(${ignored}) != 0." >&2
    echo "      Either a test newly fails to collect in-container (missing dep/file in the" >&2
    echo "      image) or the documented ignores are stale. Investigate before merging." >&2
    exit 1
fi

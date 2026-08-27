"""Shared helpers for Alembic migration tests (mig 039 — M6.1).

These tests drive the Alembic CLI from the host against the staging DB
`onnix_dev`. They never touch onnix_prod.

The host (not the dev container) is used because the dev container's
code is frozen in the image — newly-added migration files (like 039)
do not appear inside the container until the next rebuild. Tests run
on the host where the live `panel/alembic/versions/` tree is current.

Helper pattern: shell-out to `alembic ...` from <raíz del repo>/panel/
with POSTGRES_HOST=127.0.0.1 + POSTGRES_DB=onnix_dev injected so the
env.py reads onnix_dev (not prod).

Plan 115-03 — added `_restore_head_040_after_migration_test` autouse
fixture so migration tests can never leak a downgraded schema (e.g. 039
without the visits table) into downstream suites under random ordering.
This matters now that `contact_service.get_contact_detail` queries the
visits table on every contact_detail request — without restoration, any
test that hits `/contacts/{id}` after a 039 downgrade would crash with
`relation "visits" does not exist`.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PG_CONTAINER = "onnix-postgres"
DEV_DB = os.environ.get("POSTGRES_DB", "onnix_dev")
PG_USER = "onnix"

# STAB-03 (TD-115-04) — scratch-DB redirection.
#
# The roundtrip tests drop/re-add `contacts` columns on every cycle, which
# permanently bloats `pg_attribute.attnum` (dead slots are never reclaimed).
# Run against onnix_dev, that inflated dev's contacts attnum from ~9
# (prod baseline) to 500+. The fix: a session-scoped scratch DB
# (`onnix_test_mig_<pid>`) absorbs ALL migration-test mutations, then is
# dropped — so onnix_dev is never touched by these tests.
#
# `TARGET_DB` is the live target every helper (alembic_cmd / psql /
# current_alembic_head) reads. It defaults to DEV_DB so any non-fixtured
# usage still behaves as before; the `_migration_scratch_db` session fixture
# flips it to the scratch DB name for the duration of the migration suite.
TARGET_DB = DEV_DB

# Resolve panel/ directory robustly regardless of pytest invocation cwd.
PANEL_DIR = str(Path(__file__).resolve().parent.parent.parent)


def alembic_cmd(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run `alembic <args>` from the panel/ directory against TARGET_DB.

    TARGET_DB is onnix_dev by default and the scratch DB while the
    migration suite runs (STAB-03). The alembic env.py honors POSTGRES_DB.

    Se invoca como `python -m alembic` y no como `alembic` a secas: el binario
    del venv solo esta en el PATH si alguien lo activo primero, y la suite se
    corre con la ruta completa al interprete (`.venv/bin/python -m pytest`).
    Sin esto los 22 tests de esta carpeta morian con FileNotFoundError sobre
    'alembic' — 22 rojos que no decian nada de las migraciones. sys.executable
    es siempre el mismo interprete que esta corriendo pytest, asi que funciona
    igual en el host, adentro del contenedor y con el venv activado o no.

    Returns the CompletedProcess; tests must inspect .returncode / .stdout / .stderr.
    """
    env = os.environ.copy()
    env["POSTGRES_HOST"] = "127.0.0.1"
    env["POSTGRES_DB"] = TARGET_DB
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PANEL_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def psql(sql: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a SQL statement against TARGET_DB via psql in the postgres container.

    TARGET_DB is onnix_dev by default and the scratch DB while the
    migration suite runs (STAB-03).
    """
    return subprocess.run(
        [
            "docker", "exec", PG_CONTAINER,
            "psql", "-U", PG_USER, "-d", TARGET_DB, "-tA", "-c", sql,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _admin_psql(sql: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a SQL statement against the `postgres` maintenance DB.

    Used for CREATE/DROP DATABASE of the scratch DB (cannot run those from
    inside the target DB connection). NEVER targets a real DB for DDL.
    """
    return _admin_psql_on("postgres", sql, timeout=timeout)


def _admin_psql_on(db: str, sql: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a SQL statement against an explicit DB (psql in the postgres container).

    Used for the scratch-DB stamp (db = scratch name) and for maintenance-DB
    DDL (db = "postgres"). DDL-on-scratch only; callers pass the scratch name.
    """
    return subprocess.run(
        [
            "docker", "exec", PG_CONTAINER,
            "psql", "-U", PG_USER, "-d", db, "-tA", "-c", sql,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def current_alembic_head() -> str:
    """Return the version_num currently stored in alembic_version on onnix_dev."""
    res = psql("SELECT version_num FROM alembic_version;")
    return (res.stdout or "").strip()


def _step_down_from_041_if_needed() -> str:
    """Normalize a starting head of 041 down to 040.

    Migration 041 (M6.3) is a pure bot_settings data-seed above 040 with no
    schema change. The 038/039/040 schema-roundtrip helpers only recognize
    heads 038/039/040, so if the scratch DB is at 041 we step it down to 040
    first and let the existing logic take over. Returns the resulting head.
    """
    head = current_alembic_head()
    if head == "041_seed_bot_default_mode":
        res = alembic_cmd("downgrade", "040_visits")
        if res.returncode != 0:
            raise RuntimeError(
                f"alembic downgrade -1 (041->040) failed:\n"
                f"stdout={res.stdout}\nstderr={res.stderr}"
            )
        head = current_alembic_head()
    return head


def ensure_head_039() -> None:
    """Make sure DB ends at HEAD 039_roles_auth_audit before a test.

    From 040: downgrade to 039 (clearing any visit_scheduled rows first to
    bypass the mig 040 downgrade guard on stale test state). The 039 schema
    is preserved untouched by mig 040 (which only adds the visits table +
    widens contacts_status_check), so callers relying on 039 introspection
    work identically whether we arrive from 038 or 040.
    From 038: upgrade to 039 specifically (not head — keeps callers in the
    head they expect).
    From 039: no-op.
    """
    head = _step_down_from_041_if_needed()
    if head == "039_roles_auth_audit":
        return
    if head == "040_visits":
        psql(
            "UPDATE contacts SET status = 'interested' "
            "WHERE status = 'visit_scheduled' AND phone LIKE 'pytest_%';"
        )
        res = alembic_cmd("downgrade", "-1")
        if res.returncode != 0:
            raise RuntimeError(
                f"alembic downgrade -1 (040->039) failed:\nstdout={res.stdout}\nstderr={res.stderr}"
            )
        return
    if head == "038_seed_chatbot_flag":
        res = alembic_cmd("upgrade", "039_roles_auth_audit")
        if res.returncode != 0:
            raise RuntimeError(
                f"alembic upgrade 039 failed:\nstdout={res.stdout}\nstderr={res.stderr}"
            )
        return
    raise RuntimeError(
        f"Unexpected alembic head before test: {head!r}. Expected 038, 039, or 040."
    )


def ensure_head_038() -> None:
    """Downgrade to 038 if currently at 039 or 040. Used by round-trip tests."""
    head = _step_down_from_041_if_needed()
    if head == "038_seed_chatbot_flag":
        return
    if head == "040_visits":
        # Two-step: 040 -> 039 -> 038. Each downgrade needs its own guard cleanup.
        psql(
            "UPDATE contacts SET status = 'interested' "
            "WHERE status = 'visit_scheduled' AND phone LIKE 'pytest_%';"
        )
        res = alembic_cmd("downgrade", "-1")
        if res.returncode != 0:
            raise RuntimeError(
                f"alembic downgrade -1 (040->039) failed:\nstdout={res.stdout}\nstderr={res.stderr}"
            )
        head = current_alembic_head()
    if head == "039_roles_auth_audit":
        psql("DELETE FROM users WHERE role = 'agent' AND email LIKE 'pytest_%';")
        res = alembic_cmd("downgrade", "-1")
        if res.returncode != 0:
            raise RuntimeError(
                f"alembic downgrade -1 (039->038) failed:\nstdout={res.stdout}\nstderr={res.stderr}"
            )
        return
    raise RuntimeError(
        f"Unexpected alembic head before test: {head!r}. Expected 038, 039, or 040."
    )


def ensure_head_040() -> None:
    """Make sure DB is at HEAD 040_visits before a test (M6.2).

    If at 039, upgrade. If already 040, no-op. Used by tests that assume
    the post-040 schema state (visits table + widened contacts_status_check).
    """
    head = _step_down_from_041_if_needed()
    if head == "040_visits":
        return
    if head in ("039_roles_auth_audit", "038_seed_chatbot_flag"):
        res = alembic_cmd("upgrade", "040_visits")
        if res.returncode != 0:
            raise RuntimeError(
                f"alembic upgrade head failed:\nstdout={res.stdout}\nstderr={res.stderr}"
            )
        return
    raise RuntimeError(
        f"Unexpected alembic head before test: {head!r}. Expected 038, 039, or 040."
    )


def ensure_head_039_from_040() -> None:
    """Downgrade to 039 if currently at 040. Used by round-trip test (M6.2)."""
    head = _step_down_from_041_if_needed()
    if head == "039_roles_auth_audit":
        return
    if head == "040_visits":
        # Cleanup defensive: clear any contacts with status='visit_scheduled'
        # so the mig 040 downgrade guard does not fire on stale test state.
        psql(
            "UPDATE contacts SET status = 'interested' "
            "WHERE status = 'visit_scheduled' AND phone LIKE 'pytest_%';"
        )
        res = alembic_cmd("downgrade", "-1")
        if res.returncode != 0:
            raise RuntimeError(
                f"alembic downgrade -1 failed:\nstdout={res.stdout}\nstderr={res.stderr}"
            )
        return
    raise RuntimeError(
        f"Unexpected alembic head before test: {head!r}. Expected 039 or 040."
    )


def _scratch_db_name() -> str:
    """Unique throwaway DB name for this pytest process."""
    return f"onnix_test_mig_{os.getpid()}"


def _drop_scratch_db(name: str) -> None:
    """Terminate any backends on `name`, then DROP DATABASE IF EXISTS.

    Defensive: a stranded connection (e.g. NullPool engine not fully torn
    down) would otherwise block the DROP. Safe-by-name: only ever called
    with a onnix_test_mig_* scratch name.
    """
    assert name.startswith("onnix_test_mig_"), (
        f"refusing to drop non-scratch DB: {name!r}"
    )
    _admin_psql(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{name}' AND pid <> pg_backend_pid();"
    )
    res = _admin_psql(f"DROP DATABASE IF EXISTS {name};")
    if res.returncode != 0:
        raise RuntimeError(
            f"Failed to drop scratch DB {name}:\n"
            f"stdout={res.stdout}\nstderr={res.stderr}"
        )


@pytest.fixture(scope="session", autouse=True)
def _migration_scratch_db():
    """STAB-03 — run ALL migration tests against a throwaway scratch DB.

    Creates `onnix_test_mig_<pid>` (TEMPLATE template0), brings it to
    HEAD via `alembic upgrade head` (full 001->040 schema so the 038/039/040
    roundtrips have their expected starting point), then points TARGET_DB at
    it for the duration of the session. After the suite, TARGET_DB is
    restored to onnix_dev and the scratch DB is dropped — taking all the
    attnum bloat with it. onnix_dev is NEVER mutated by migration tests.

    The roundtrip tests assert schema objects (tables/indexes/columns/CHECK),
    not baseline data, so a fresh scratch DB built from migrations alone is a
    sufficient — and correct — target.
    """
    global TARGET_DB

    scratch = _scratch_db_name()

    # Clean slate: drop a stale same-name scratch DB from a crashed prior run.
    _drop_scratch_db(scratch)

    create = _admin_psql(f"CREATE DATABASE {scratch} TEMPLATE template0;")
    if create.returncode != 0:
        raise RuntimeError(
            f"Failed to create scratch DB {scratch}:\n"
            f"stdout={create.stdout}\nstderr={create.stderr}"
        )

    # The dev schema references types/operators from cluster extensions
    # (vector → properties.embedding, pg_trgm → gin_trgm_ops indexes, etc.).
    # A `-n public` schema dump does NOT emit CREATE EXTENSION, so create the
    # extensions dev uses in the scratch DB before loading the schema, or the
    # `properties` table (and its dependents like `visits`' FK) fail to build.
    ext = _admin_psql_on(
        scratch,
        "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"; "
        "CREATE EXTENSION IF NOT EXISTS pgcrypto; "
        "CREATE EXTENSION IF NOT EXISTS pg_trgm; "
        "CREATE EXTENSION IF NOT EXISTS unaccent; "
        "CREATE EXTENSION IF NOT EXISTS vector;",
    )
    if ext.returncode != 0:
        _drop_scratch_db(scratch)
        raise RuntimeError(
            f"Failed to create extensions on scratch DB {scratch}:\n"
            f"stdout={ext.stdout}\nstderr={ext.stderr}"
        )

    # Seed the scratch DB with onnix_dev's PUBLIC schema (structure only,
    # zero rows). The early migrations (001+) ALTER pre-existing base tables
    # (contacts/users created outside Alembic at bootstrap), so a bare
    # `alembic upgrade head` from template0 fails — the dev schema dump gives
    # the scratch DB an exact copy of the head-040 structure to roundtrip on.
    # -n public excludes the n8n schema. --schema-only excludes all data.
    seed = subprocess.run(
        [
            "docker", "exec", PG_CONTAINER, "bash", "-c",
            "pg_dump -U {user} --schema-only --no-owner --no-privileges "
            "-n public {dev} | psql -U {user} -d {scratch}".format(
                user=PG_USER, dev=DEV_DB, scratch=scratch
            ),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if seed.returncode != 0:
        _drop_scratch_db(scratch)
        raise RuntimeError(
            f"Failed to seed scratch DB {scratch} from {DEV_DB} schema:\n"
            f"stdout={seed.stdout}\nstderr={seed.stderr}"
        )

    # The schema dump creates the (empty) alembic_version table but does NOT
    # carry the version row (that is data). Stamp it to the dev schema's head
    # so the roundtrip helpers see the expected starting point (HEAD 040).
    stamp = _admin_psql_on(
        scratch,
        "INSERT INTO alembic_version (version_num) VALUES ('040_visits') "
        "ON CONFLICT DO NOTHING;",
    )
    if stamp.returncode != 0:
        _drop_scratch_db(scratch)
        raise RuntimeError(
            f"Failed to stamp alembic_version on scratch DB {scratch}:\n"
            f"stdout={stamp.stdout}\nstderr={stamp.stderr}"
        )

    previous_target = TARGET_DB
    TARGET_DB = scratch
    try:
        yield scratch
    finally:
        TARGET_DB = previous_target
        _drop_scratch_db(scratch)


@pytest.fixture(autouse=True)
def _restore_head_040_after_migration_test():
    """Plan 115-03 — guarantee every migration test ends with DB @ 040 (head).

    Without this, a downgrade-leaning migration test that runs BEFORE a
    contacts/visits route test (under pytest-randomly) leaves the visits
    table dropped. Downstream tests then crash with `relation "visits"
    does not exist` because contact_service.get_contact_detail now calls
    visit_repo.has_active_for_contact on every detail load (M6.2 §5.10).

    Idempotent — `ensure_head_040` no-ops when DB is already at 040.
    """
    yield
    try:
        head = current_alembic_head()
        if head == "040_visits":
            return
        # 041 is a pure bot_settings data-seed above 040 (no schema change).
        # Migration tests assert the 038/039/040 schema, so the stable
        # post-test baseline is 040 — step 041 -> 040 if we're above it.
        if head == "041_seed_bot_default_mode":
            res = alembic_cmd("downgrade", "040_visits")
            if res.returncode != 0:
                raise RuntimeError(
                    f"Post-test downgrade 041->040 failed:\n"
                    f"stdout={res.stdout}\nstderr={res.stderr}"
                )
            return
        # Defensive: clear stale visit_scheduled rows so a re-upgrade onto
        # an existing 'pytest_*' contact doesn't trip mig 040's downgrade
        # guard if a future test downgrades again. Target 040 explicitly
        # (NOT head) so the 041 seed is not applied to the test baseline.
        if head in ("039_roles_auth_audit", "038_seed_chatbot_flag"):
            res = alembic_cmd("upgrade", "040_visits")
            if res.returncode != 0:
                raise RuntimeError(
                    f"Post-test upgrade to 040 failed:\n"
                    f"stdout={res.stdout}\nstderr={res.stderr}"
                )
    except Exception as exc:  # pragma: no cover — best-effort
        # Don't mask the test's own failure with a teardown crash.
        # Log and continue; the next migration test will repair if needed.
        import warnings
        warnings.warn(f"Migration test teardown could not restore HEAD 040: {exc}")

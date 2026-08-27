"""
Shared fixtures for Onnix SA panel tests.

All tests run against the onnix_dev database (never production).
Test data uses email pattern 'pytest_*@onnixtest.com' for easy cleanup.
"""
import fcntl
import os
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env early so TEST_ADMIN_PASSWORD and other vars are available to fixtures
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

# Must set POSTGRES_HOST before any app import so the engine uses localhost
os.environ["POSTGRES_HOST"] = "127.0.0.1"

# --------------------------------------------------------------------------
# Una base por worker de xdist (fase 6 del CICD_PLAN / TD-OPS-04)
# --------------------------------------------------------------------------
# Sin xdist nada cambia: onnix_dev, como siempre. Con `-n N`, cada worker
# recibe su propia `onnix_test_gw_<n>`, creada desde cero por
# scripts/make_test_db.sh. Dos razones, y la segunda importa más que la
# velocidad: (1) la suite TRUNCA y DELETEa estado compartido, así que dos
# workers sobre una misma base se destruyen entre sí; (2) onnix_dev es la
# base que sirve staging — con bases por worker la suite deja de escribirle.
#
# El nombre entra a propósito en el patrón que tests/_guards.py ya acepta
# (`^onnix_test_[a-z]+_\d+$`): el guard no se toca ni se afloja.
_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
TEST_DB = (
    f"onnix_test_gw_{_XDIST_WORKER[2:]}"
    if _XDIST_WORKER.startswith("gw")
    else "onnix_dev"
)
# NEVER run tests against onnix_prod.
os.environ["POSTGRES_DB"] = TEST_DB
os.environ.setdefault("PANEL_SECRET_KEY", "test-secret-key-for-pytest-only")
# Silence external notifications — prevent tests from sending real Telegram/Twilio messages.
# Must be set BEFORE app imports because bot_settings reads env at class definition time.
os.environ["TELEGRAM_EZ_CHAT_ID"] = ""
os.environ["FOLLOWUP_SENDER_ENABLED"] = "false"
# Signal to app modules imported below that we are in a pytest session.
# Used by main.py to set https_only=False on SessionMiddleware so that
# test HTTP clients (base_url="http://test") can receive session cookies.
os.environ.setdefault("PYTEST_CURRENT_TEST", "collecting")

# Ensure panel/ is on sys.path so 'from app.X import ...' works
_panel_dir = str(Path(__file__).resolve().parent.parent)
if _panel_dir not in sys.path:
    sys.path.insert(0, _panel_dir)

# Raíz del repo, resuelta desde este archivo y no escrita a mano. Hasta el
# 2026-08-18 medio centenar de tests decían "/home/onnix" porque el repo
# vivía en el home del VPS; cuando el repo se mudó a /srv/onnix, 54 tests se
# pusieron rojos por una ruta, no por el código que decían cubrir.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
os.environ["GEO_DATA_PATH"] = str(REPO_ROOT / "data" / "geografia")


# --------------------------------------------------------------------------
# El candado que sobrevive a quien lo escribió
# --------------------------------------------------------------------------
# Dos sesiones de pytest sobre la MISMA base se destruyen entre sí y el
# resultado no dice por qué: 67 fallos fantasma, ya pasó. Con xdist el riesgo
# se multiplica — un mapeo worker→base mal hecho no se ve, sólo ensucia.
#
# El flock es exclusivo POR BASE y lo tiene el proceso mientras dure la sesión:
# si dos workers (o dos corridas) apuntan a la misma base, el segundo aborta
# acá, ruidoso, en vez de entregar resultados sucios en silencio.
_DB_LOCK_PATH = f"/tmp/onnix-pytest-{TEST_DB}.lock"
_db_lock = open(_DB_LOCK_PATH, "a+")  # noqa: SIM115 — se mantiene abierto a propósito
try:
    fcntl.flock(_db_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError as exc:
    raise RuntimeError(
        f"ABORTADO: otro proceso de pytest ya tiene tomada la base '{TEST_DB}' "
        f"(candado {_DB_LOCK_PATH}). Dos sesiones sobre la misma base se "
        f"destruyen entre sí. Si esto salta con -n, dos workers quedaron "
        f"apuntando a la misma base: revisar TEST_DB en tests/conftest.py."
    ) from exc


if _XDIST_WORKER:
    # Cada worker se construye su base. ~6 s, en paralelo con los demás.
    # check=True: si la base no se pudo construir, la corrida muere acá y lo
    # dice — no arranca contra una base a medias.
    subprocess.run(
        [str(REPO_ROOT / "scripts" / "make_test_db.sh"), TEST_DB],
        check=True,
        timeout=300,
        env={**os.environ, "PYTHON": sys.executable},
    )

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.config import settings
from app.database import get_db
from app.main import app

# ---------------------------------------------------------------------------
# Test engine — NullPool: fresh connection per session, no reuse between tests
# ---------------------------------------------------------------------------

_test_engine = create_async_engine(
    settings.DATABASE_URL,
    poolclass=NullPool,
    echo=False,
)
_TestSession = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)


def pytest_sessionfinish(session, exitstatus):
    """Dispose all async engines after tests complete to prevent teardown hang.

    Both the test engine (NullPool) and the app's production engine (imported
    at module level by app.database) must be disposed — otherwise their async
    connection pools keep the event loop alive indefinitely at teardown.

    Uses a synchronous hook with asyncio.run() to avoid event-loop-lifecycle
    issues that make session-scoped async fixtures unreliable for cleanup.
    """
    import asyncio
    from app.database import engine as app_engine

    async def _cleanup():
        await _test_engine.dispose()
        await app_engine.dispose()

    try:
        asyncio.run(_cleanup())
    except Exception:
        pass  # Best-effort cleanup — don't block exit


async def _override_get_db():
    """Replace the app's pooled engine with NullPool for tests.

    This prevents 'Future attached to a different loop' errors that occur
    when pytest-asyncio creates a new event loop per test while the app's
    connection pool still holds connections bound to the previous loop.
    """
    session = _TestSession()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# Apply override globally — affects all ASGI requests in every test
app.dependency_overrides[get_db] = _override_get_db

TEST_EMAIL_PATTERN = "pytest_%@onnixtest.com"


def _psql(sql: str) -> None:
    """Run a SQL statement via psql inside the postgres container (sync).

    Always targets TEST_DB (onnix_dev, o la base del worker de xdist) —
    tests must never touch onnix_prod.
    Uses Popen with explicit kill to avoid hangs when docker exec stalls.

    NOTE: this is the LEGACY, non-checked helper. It swallows stdout/stderr and
    does NOT raise on a non-zero psql return. It is kept ONLY for the login
    fixtures (`user_client` / `agent_client` INSERTs), where a transient failure
    is tolerable. The session CLEANUP path must use `_psql_checked` instead —
    cleanup must be fail-loud (STAB-02 / TD-115-05).
    """
    try:
        proc = subprocess.Popen(
            ["docker", "exec", "onnix-postgres",
             "psql", "-U", "onnix", "-d", TEST_DB, "-c", sql],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        proc.wait(timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        # Kill the hung process and reap it
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass


def _psql_checked(sql: str, *, timeout: int = 30) -> str:
    """Run SQL via psql in the postgres container — FAIL-LOUD (STAB-02 / TD-115-05).

    Unlike `_psql`, this:
      * passes ``-v ON_ERROR_STOP=1`` so a mid-batch SQL error makes psql exit
        non-zero (psql otherwise returns 0 even when a statement failed);
      * captures stdout/stderr (no DEVNULL swallow);
      * RAISES ``RuntimeError`` on a non-zero return code OR on timeout, so an
        FK-abort / SQL error can never silently leave test rows behind.

    Always targets TEST_DB. Returns stdout (handy for residual COUNT reads).
    The ``timeout`` keeps the original hang-guard behavior.
    """
    try:
        result = subprocess.run(
            ["docker", "exec", "onnix-postgres",
             "psql", "-U", "onnix", "-d", TEST_DB,
             "-v", "ON_ERROR_STOP=1", "-c", sql],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"cleanup psql timed out after {timeout}s: {sql[:120]}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"cleanup psql failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


# ---------------------------------------------------------------------------
# DB session fixture — NullPool ensures no cross-test connection sharing
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db() -> AsyncSession:
    session = _TestSession()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# HTTP clients
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        yield c


ADMIN_EMAIL = "ez@onnix.com.py"


def _clear_admin_lockout() -> None:
    """Clear the real admin's recent lockout markers before logging in.

    The real admin (id 1) is shared seed data, and route-level wrong-password
    tests (test_routes_auth, test_routes_users) write real `wrong_password` rows
    to auth_audit. Because cleanup_test_data is session-scoped, those rows
    persist across rapid re-runs and can cross the 5-in-15min lockout threshold,
    after which EVERY admin login is blocked (writes a `locked` row) and all
    admin-authenticated tests cascade to redirect-to-login failures. An
    "authenticated admin session" fixture being locked is contradictory by
    definition. Scoped to this email + recent window; never touches the lockout
    feature tests (they use test_user_email).
    """
    _psql(
        f"DELETE FROM auth_audit WHERE email = '{ADMIN_EMAIL}' "
        "AND result IN ('wrong_password', 'not_found', 'inactive', 'locked') "
        "AND created_at > now() - interval '40 minutes'"
    )


def _login_cookies(email: str, password: str) -> dict:
    """Perform ONE real /login and return the resulting cookie jar as a dict.

    Synchronous on purpose: it drives its own event loop via asyncio.run(), the
    same pattern pytest_sessionfinish already uses here, because session-scoped
    async fixtures are unreliable across pytest-asyncio's per-test loops. Safe
    with NullPool — the asyncpg connection opened by the login is closed inside
    that loop.
    """
    import asyncio

    async def _go() -> dict:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            resp = await c.post("/login", data={"email": email, "password": password})
            assert resp.status_code == 303, (
                f"login de {email} devolvio {resp.status_code}, se esperaba 303. "
                "Revisar TEST_ADMIN_PASSWORD (ver CLAUDE.md, trampas conocidas)."
            )
            assert "onnix_session" in c.cookies, (
                f"login de {email} no dejo cookie de sesion onnix_session"
            )
            return dict(c.cookies)

    return asyncio.run(_go())


@pytest.fixture(scope="session")
def _admin_cookies(cleanup_test_data) -> dict:
    """ONE real admin login per session; every admin_client replays its cookie.

    The panel's session is a signed, stateless cookie (SessionMiddleware in
    app/main.py), so replaying it in a fresh AsyncClient is indistinguishable
    from having logged in — nothing is stored server-side to go stale. It stays
    valid for the whole run: SESSION_INACTIVITY_MINUTES is 60 and no test bumps
    the real admin's pw_changed_at (the self-password-change test creates its
    own admin on purpose).

    Why: 391 tests asked for admin_client, and each one paid a docker exec psql
    (229 ms) plus a real bcrypt cost-12 verification (359 ms) — 25% of the whole
    suite spent re-proving the same login. Tests that need a REAL login (logout,
    lockout, CSRF rotation) use `admin_client_fresh`.
    """
    _clear_admin_lockout()
    return _login_cookies(ADMIN_EMAIL, os.environ.get("TEST_ADMIN_PASSWORD", "test-fallback-only"))


@pytest_asyncio.fixture
async def admin_client(_admin_cookies) -> AsyncClient:
    """HTTP client with an active admin session (Ez), from the cached cookie."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
        cookies=_admin_cookies,
    ) as c:
        yield c


@pytest_asyncio.fixture
async def admin_client_fresh() -> AsyncClient:
    """HTTP client that performs a REAL admin login for this test alone.

    Use it when the test cares about the login itself — session rotation, the
    lockout counter, CSRF token rotation on login — where replaying a cached
    cookie would prove nothing.
    """
    _clear_admin_lockout()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": ADMIN_EMAIL,
            "password": os.environ.get("TEST_ADMIN_PASSWORD", "test-fallback-only"),
        })
        yield c


@pytest_asyncio.fixture
async def user_client() -> AsyncClient:
    """HTTP client with an active non-admin session (temp test user)."""
    _psql(
        "INSERT INTO users (email, name, role, password_hash, is_active) "
        "VALUES ('pytest_user@onnixtest.com', 'Test User', 'user', "
        "'$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu', true) "
        "ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash, is_active = true"
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_user@onnixtest.com",
            "password": "test123",
        })
        yield c


@pytest_asyncio.fixture
async def agent_client() -> AsyncClient:
    """HTTP client with an active agent session (pytest_agent@onnixtest.com).

    Password hash matches 'test123' (bcrypt, cost 12).
    """
    _psql(
        "INSERT INTO users (email, name, role, password_hash, is_active) "
        "VALUES ('pytest_agent@onnixtest.com', 'Test Agent', 'agent', "
        "'$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu', true) "
        "ON CONFLICT (email) DO UPDATE SET "
        "role=EXCLUDED.role, password_hash=EXCLUDED.password_hash, is_active=true"
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_agent@onnixtest.com",
            "password": "test123",
        })
        yield c


# ---------------------------------------------------------------------------
# Cleanup: synchronous psql call — no async session conflict
# ---------------------------------------------------------------------------

# Phone prefix used exclusively by tests — must not overlap with real data.
# All test-created contacts use +5959819XXXXXXX or +5959818XXXXXXX range.
TEST_PHONE_PREFIX_SQL = "phone LIKE '+5959815%' OR phone LIKE '+5959816%' OR phone LIKE '+5959817%' OR phone LIKE '+5959818%' OR phone LIKE '+5959819%'"

# Phone-NULL test contacts (leak destapado en M6.5, pre-existente):
# tests/test_routes_leads_agent_tab.py inserts contacts with phone=None which
# the phone-prefix pattern above can NEVER match — they accumulated in
# onnix_dev across runs and (last_activity_at=2099 sorts first) pushed other
# tests' fixtures off page 1 of the asignados tab. The tests now clean up after
# themselves (yield+teardown fixture); this predicate is the durable DEFENSE.
# Keyed on the exact test-only names + source='manual' + phone IS NULL so no
# real contact can ever match (real manual contacts have phones).
TEST_NULL_PHONE_CONTACTS_SQL = (
    "(phone IS NULL AND source = 'manual' "
    "AND name IN ('AssignedLead', 'ShouldNotAppear'))"
)

# Full test-contact predicate used by the cleanup DELETEs and residual checks.
TEST_CONTACTS_SQL = f"({TEST_PHONE_PREFIX_SQL}) OR {TEST_NULL_PHONE_CONTACTS_SQL}"

# STAB-04 (119-06) — durable guard-safe agent-leftover cleanup rule.
#
# A leftover NON-pytest agent user `agent_uat@onnix.com.py` (created
# during M6.2 manual UAT) broke migration 039's downgrade guard, which aborts
# on ANY `role='agent'` row — failing test_039 roundtrip deterministically
# across every seed (Phase 117). The one-time row was purged from onnix_dev;
# this rule makes the cleanup DURABLE so a future run can never re-strand a
# test/UAT agent that trips the guard.
#
# WHY KEY ON EMAIL, NOT ROLE: future real agents MUST survive — prod (and one
# day dev) may hold legitimate `role='agent'` users. A "DELETE all role='agent'"
# cleanup would delete them. So the rule keys on the TEST-EMAIL PATTERN
# ('pytest_%@onnixtest.com') + the specific KNOWN UAT leftover email below, never
# on `role`. NEVER expand this to delete the 4 real users (admin id1, ez id3,
# admin id4, alexis id6) or any non-test agent.
#
# The migrations/test_039 `_cleanup_agents_around_test` fixture stays AS-IS (it
# scrubs pytest_% agents around its own test); this session-level rule covers
# the non-pytest UAT leftover so the roundtrip's guard passes regardless of
# test order. The delete runs inside the fail-loud `_psql_checked` path and is
# counted by the post-cleanup residual assert (agent_uat must be 0 after).
STRAY_UAT_AGENT_EMAIL = "agent_uat@onnix.com.py"

# ---------------------------------------------------------------------------
# Locked per-table cleanup strategy (OQ-1 = Option A hardened, Phase 118):
#   visits .................................. TRUNCATE (only TRUNCATE-safe table; 0 baseline rows)
#   contacts/lead_events/messages/
#     conversations/contact_notes ........... pattern-DELETE by pytest phone / child contact_ids
#   contact_reminders ....................... DELETE by pytest user_id OR contact_id (mig-044)
#   users/auth_audit ........................ email-pattern-DELETE 'pytest_%@onnixtest.com'
#   properties/bot_settings ................. NEVER TOUCHED
# Child-first FK order. Runs via the FAIL-LOUD `_psql_checked` helper.
# ---------------------------------------------------------------------------

# Ten tables the residual assert guards (must match the cleanup above).
_RESIDUAL_CHECKS = {
    "lead_events": f"SELECT count(*) FROM lead_events WHERE contact_id IN (SELECT id FROM contacts WHERE {TEST_CONTACTS_SQL})",
    "messages": f"SELECT count(*) FROM messages WHERE contact_id IN (SELECT id FROM contacts WHERE {TEST_CONTACTS_SQL})",
    "conversations": f"SELECT count(*) FROM conversations WHERE contact_id IN (SELECT id FROM contacts WHERE {TEST_CONTACTS_SQL})",
    "contact_notes": f"SELECT count(*) FROM contact_notes WHERE contact_id IN (SELECT id FROM contacts WHERE {TEST_CONTACTS_SQL})",
    "contact_reminders": (
        f"SELECT count(*) FROM contact_reminders WHERE "
        f"user_id IN (SELECT id FROM users WHERE email LIKE 'pytest_%@onnixtest.com') "
        f"OR contact_id IN (SELECT id FROM contacts WHERE {TEST_CONTACTS_SQL})"
    ),
    "contacts": f"SELECT count(*) FROM contacts WHERE {TEST_CONTACTS_SQL}",
    "auth_audit": "SELECT count(*) FROM auth_audit WHERE email LIKE 'pytest_%@onnixtest.com'",
    "users": f"SELECT count(*) FROM users WHERE email LIKE 'pytest_%@onnixtest.com' OR email = '{STRAY_UAT_AGENT_EMAIL}'",
    "visits": "SELECT count(*) FROM visits",
}


def _build_cleanup_sql() -> str:
    """Build the locked per-table cleanup SQL (child-first FK order)."""
    return (
        # visits: ONLY TRUNCATE-safe table (0 baseline rows). Done first so the
        # FK refs to users/contacts are gone before those deletes.
        "TRUNCATE visits; "
        # M6.1 added ON DELETE RESTRICT on contacts.agent_user_id → users.
        # NULL the references before deleting users, otherwise the user DELETE
        # aborts. Covers pytest users AND the known stray UAT agent.
        f"UPDATE contacts SET agent_user_id = NULL WHERE agent_user_id IN "
        f"(SELECT id FROM users WHERE email LIKE 'pytest_%@onnixtest.com' "
        f"OR email = '{STRAY_UAT_AGENT_EMAIL}'); "
        # auth_audit (M6.1) references email of pytest users — non-FK path.
        f"DELETE FROM auth_audit WHERE email LIKE 'pytest_%@onnixtest.com'; "
        # contact_notes.user_id references users(id) with RESTRICT — must delete
        # (or null) notes created by pytest users BEFORE deleting those users.
        # Covers feat/agent-authz tests that create notes with pytest agent users.
        f"DELETE FROM contact_notes WHERE user_id IN "
        f"(SELECT id FROM users WHERE email LIKE 'pytest_%@onnixtest.com' "
        f"OR email = '{STRAY_UAT_AGENT_EMAIL}'); "
        # contact_reminders.user_id has no ON DELETE CASCADE (mig-044) — must
        # delete rows referencing pytest users OR pytest contacts BEFORE deleting
        # users/contacts, else the user DELETE aborts with FK violation.
        f"DELETE FROM contact_reminders WHERE "
        f"user_id IN (SELECT id FROM users WHERE email LIKE 'pytest_%@onnixtest.com') "
        f"OR contact_id IN (SELECT id FROM contacts WHERE {TEST_CONTACTS_SQL}); "
        # users: pytest users + the one known stray UAT agent. Never the 4 real users.
        f"DELETE FROM users WHERE email LIKE 'pytest_%@onnixtest.com' "
        f"OR email = '{STRAY_UAT_AGENT_EMAIL}'; "
        # contacts children (child-first FK order), then contacts.
        f"DELETE FROM contact_notes WHERE contact_id IN "
        f"(SELECT id FROM contacts WHERE {TEST_CONTACTS_SQL}); "
        f"DELETE FROM lead_events WHERE contact_id IN "
        f"(SELECT id FROM contacts WHERE {TEST_CONTACTS_SQL}); "
        f"DELETE FROM messages WHERE contact_id IN "
        f"(SELECT id FROM contacts WHERE {TEST_CONTACTS_SQL}); "
        f"DELETE FROM conversations WHERE contact_id IN "
        f"(SELECT id FROM contacts WHERE {TEST_CONTACTS_SQL}); "
        f"DELETE FROM contacts WHERE {TEST_CONTACTS_SQL};"
    )


def _residual_pytest_rows() -> dict[str, int]:
    """Return {table: count} for any residual pytest rows across the 9 tables.

    Empty dict ⇒ cleanup was complete. Used by the post-cleanup assert and by
    the fail-loud meta-test. Runs a single batched COUNT query via the checked
    helper (fail-loud if the query itself errors).
    """
    parts = [
        f"SELECT '{tbl}' AS t, ({q}) AS c"
        for tbl, q in _RESIDUAL_CHECKS.items()
    ]
    sql = " UNION ALL ".join(parts) + ";"
    out = _psql_checked(
        f"COPY ({sql.rstrip(';')}) TO STDOUT WITH (FORMAT csv)"
    )
    leftovers: dict[str, int] = {}
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        tbl, _, cnt = line.partition(",")
        count = int(cnt)
        if count > 0:
            leftovers[tbl] = count
    return leftovers


def _assert_no_residual_pytest_rows() -> None:
    """FAIL the run if any residual pytest rows remain after cleanup."""
    leftovers = _residual_pytest_rows()
    if leftovers:
        detail = ", ".join(f"{t}={c}" for t, c in sorted(leftovers.items()))
        raise RuntimeError(
            f"conftest cleanup left residual pytest rows: {detail}"
        )


def _run_cleanup() -> None:
    """Run the fail-loud per-table cleanup, then assert 0 residual rows.

    Any non-zero psql return (FK abort, SQL error) raises immediately via
    `_psql_checked`; the post-cleanup assert then guarantees zero pytest rows
    survive across the 9 tables (else the whole run fails with the leftover list).
    """
    _psql_checked(_build_cleanup_sql())
    _assert_no_residual_pytest_rows()


@pytest.fixture(autouse=True)
def reset_contact_count_cache():
    """Clear the in-process COUNT cache before every test.

    contact_service._COUNT_CACHE is module-level state with a 30 s TTL.
    Tests that insert contacts directly via the repository (bypassing the
    service write paths) do not trigger cache invalidation, so a stale
    count from one test can bleed into subsequent tests and produce wrong
    total_pages calculations.  Clearing it at function scope removes this
    ordering dependency entirely.
    """
    from app.services import contact_service
    contact_service.clear_count_cache()


@pytest.fixture(autouse=True, scope="session")
def guard_base_de_test():
    """Aborta la corrida si el engine no quedo en una base de test.

    Pieza 0: el conftest setea POSTGRES_DB y el docstring de arriba dice
    «never production», pero eso es una intencion. Esto pregunta
    `current_database()` por la conexion REAL y aborta antes de que
    `cleanup_test_data` corra su primer DELETE.

    Va por psql adentro del contenedor —el mismo camino que usa el cleanup—
    porque es sincrono y corre antes de que exista un event loop.
    """
    from tests._guards import assert_base_de_test

    res = subprocess.run(
        ["docker", "exec", "onnix-postgres", "psql", "-U", "onnix",
         "-d", os.environ["POSTGRES_DB"], "-tA", "-c", "SELECT current_database()"],
        capture_output=True, text=True, timeout=30,
    )
    if res.returncode != 0:
        pytest.exit(f"No se pudo verificar la base de test: {res.stderr.strip()}", 1)
    try:
        assert_base_de_test((res.stdout or "").strip())
    except RuntimeError as exc:
        pytest.exit(str(exc), 1)


@pytest.fixture(autouse=True, scope="session")
def cleanup_test_data(guard_base_de_test):
    """Delete test users and test contacts (phone prefix +595981[5-9]…).

    Runs once at the START of the session (before any tests) and again at the
    END (after all tests). Session scope avoids per-test docker exec calls
    which can hang due to subprocess/waitpid issues under pytest.

    FAIL-LOUD (STAB-02 / TD-115-05): cleanup runs via `_psql_checked`
    (raises on any non-zero psql return — no silent swallow), and a
    post-cleanup assert fails the run if any residual pytest rows remain.
    Per-table strategy is locked (Phase 118 OQ-1 = Option A hardened):
    visits=TRUNCATE; contacts/lead_events/messages/conversations/contact_notes
    =pattern-DELETE; users/auth_audit=email-pattern-DELETE (+ stray UAT agent);
    properties/bot_settings NEVER TOUCHED.

    STAB-04 (TD-115-05 / test isolation): the users cleanup keys on the
    test-email pattern + the known UAT leftover email (see STRAY_UAT_AGENT_EMAIL),
    NOT on `role='agent'`, so future real agents survive while no test/UAT agent
    can re-strand and break migration 039's downgrade guard.
    """
    _run_cleanup()  # Clean + assert before tests
    yield
    _run_cleanup()  # Clean + assert after tests

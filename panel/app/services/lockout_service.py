"""
Lockout service — M6.1 (Phase 111-02).

Spec: .planning/phases/110-m6.1-plan-roles-auth/110-01-PLAN.md §4, §5.

Reglas (D-2 email-only):
- 5 fallos del mismo email en 15 min → lock 30 min.
- Alerta Telegram al cruzar threshold (idempotente: 1 alerta por ventana 30min).
- Lock derivado de auth_audit (no hay columna users.locked_until).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Spec §4 constants — locked decisions.
LOCKOUT_WINDOW_MINUTES = 15
LOCKOUT_DURATION_MINUTES = 30
LOCKOUT_THRESHOLD = 5
USER_AGENT_MAX_LEN = 1024

# Valid result values mirror the CHECK constraint on auth_audit.result.
ALLOWED_RESULTS: frozenset[str] = frozenset(
    {"success", "wrong_password", "inactive", "not_found", "locked"}
)


async def is_locked(db: AsyncSession, email: str) -> bool:
    """Return True if `email` is currently locked.

    Two detection paths cover the full 30-min lock duration:

    Path 1 (minutes 0-15 after the fail burst):
      Count failure rows in the last LOCKOUT_WINDOW_MINUTES (15 min). If >=
      LOCKOUT_THRESHOLD and the most recent failure is within
      LOCKOUT_DURATION_MINUTES (30 min), the lock is active.

    Path 2 (minutes 15-30 after the fail burst):
      Once the fail-burst rows slide outside the 15-min window, path 1 returns
      False even though the 30-min lock should still be active. Path 2 detects
      this case by looking for a result='locked' marker row within the last 30
      min that has not been superseded by an admin-unlock success row.

    Spec §4.2 (extended fix: effective duration 30 min, not 15 min).
    """
    # ---- Path 1: active fail burst still within 15-min window ----
    sql_path1 = text(
        """
        WITH last_unlock AS (
            SELECT MAX(created_at) AS at
            FROM auth_audit
            WHERE email = :email
              AND result = 'success'
              AND ip = 'admin-unlock'
              AND created_at > now() - (:window_minutes * INTERVAL '1 minute')
        ),
        recent_fails AS (
            SELECT created_at
            FROM auth_audit, last_unlock
            WHERE email = :email
              AND result IN ('wrong_password', 'inactive', 'not_found')
              AND created_at > now() - (:window_minutes * INTERVAL '1 minute')
              AND (last_unlock.at IS NULL OR created_at > last_unlock.at)
        )
        SELECT
            COUNT(*) AS fail_count,
            MAX(created_at) AS last_fail_at
        FROM recent_fails
        """
    )
    row1 = (
        await db.execute(
            sql_path1,
            {"email": email, "window_minutes": LOCKOUT_WINDOW_MINUTES},
        )
    ).one()
    fail_count: int = row1.fail_count or 0
    last_fail_at = row1.last_fail_at

    if fail_count >= LOCKOUT_THRESHOLD and last_fail_at is not None:
        now = datetime.now(timezone.utc)
        lock_until = last_fail_at + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
        if now < lock_until:
            return True

    # ---- Path 2: fail burst slid past 15-min window but lock still active ----
    # Look for a result='locked' marker row in the last 30 min. If found and
    # no admin-unlock success row has been written AFTER it, the lock persists.
    sql_path2 = text(
        """
        SELECT
            MAX(created_at) FILTER (WHERE result = 'locked') AS last_locked_at,
            MAX(created_at) FILTER (WHERE result = 'success' AND ip = 'admin-unlock')
                AS last_unlock_at
        FROM auth_audit
        WHERE email = :email
          AND created_at > now() - (:dur * INTERVAL '1 minute')
        """
    )
    row2 = (
        await db.execute(
            sql_path2,
            {"email": email, "dur": LOCKOUT_DURATION_MINUTES},
        )
    ).one()

    if row2.last_locked_at is not None:
        # A locked marker exists within the 30-min window.
        # Consider unlocked only if admin-unlock happened AFTER the lock marker.
        if row2.last_unlock_at is None or row2.last_unlock_at < row2.last_locked_at:
            return True

    return False


async def record_attempt(
    db: AsyncSession,
    email: str,
    ip: str | None,
    user_agent: str | None,
    result: str,
) -> None:
    """INSERT an auth_audit row. Spec §4.4.

    `result` must be one of ALLOWED_RESULTS (mirrors DB CHECK constraint).
    `user_agent` is truncated to USER_AGENT_MAX_LEN chars defensively.
    """
    if result not in ALLOWED_RESULTS:
        raise ValueError(
            f"lockout_service.record_attempt: invalid result {result!r}; "
            f"must be one of {sorted(ALLOWED_RESULTS)}"
        )
    ua = (user_agent or None)
    if ua is not None and len(ua) > USER_AGENT_MAX_LEN:
        ua = ua[:USER_AGENT_MAX_LEN]

    await db.execute(
        text(
            """
            INSERT INTO auth_audit (email, ip, user_agent, result)
            VALUES (:email, :ip, :user_agent, :result)
            """
        ),
        {"email": email, "ip": ip, "user_agent": ua, "result": result},
    )
    await db.commit()


async def _alert_already_fired_recently(db: AsyncSession, email: str) -> bool:
    """Idempotency guard: was a 'locked' row inserted for this email in the
    last LOCKOUT_DURATION_MINUTES? Spec §4.5.
    """
    sql = text(
        """
        SELECT COUNT(*) AS n
        FROM auth_audit
        WHERE email = :email
          AND result = 'locked'
          AND created_at > now() - (:dur * INTERVAL '1 minute')
        """
    )
    row = (
        await db.execute(
            sql, {"email": email, "dur": LOCKOUT_DURATION_MINUTES}
        )
    ).one()
    return (row.n or 0) > 0


async def _fail_count_in_window(db: AsyncSession, email: str) -> int:
    sql = text(
        """
        WITH last_unlock AS (
            SELECT MAX(created_at) AS at
            FROM auth_audit
            WHERE email = :email
              AND result = 'success'
              AND ip = 'admin-unlock'
              AND created_at > now() - (:window_minutes * INTERVAL '1 minute')
        )
        SELECT COUNT(*) AS n
        FROM auth_audit, last_unlock
        WHERE email = :email
          AND result IN ('wrong_password', 'inactive', 'not_found')
          AND created_at > now() - (:window_minutes * INTERVAL '1 minute')
          AND (last_unlock.at IS NULL OR created_at > last_unlock.at)
        """
    )
    row = (
        await db.execute(
            sql, {"email": email, "window_minutes": LOCKOUT_WINDOW_MINUTES}
        )
    ).one()
    return row.n or 0


async def maybe_trigger_lockout_alert(
    db: AsyncSession,
    email: str,
    ip: str | None,
    user_agent: str | None,
) -> None:
    """If the latest failure just crossed the threshold AND no 'locked' row
    has been inserted in the last 30 min for this email:
      1. INSERT auth_audit row with result='locked'.
      2. Fire AdminNotifier.notify_login_locked(...).

    Idempotent: subsequent calls within the 30-min lock window do NOT
    re-insert/re-alert (one alert per lock event). Spec §4.5, §5.1.

    Called from route's failure path AFTER record_attempt has already
    written the latest failure row. Safe to call on every failure — the
    threshold + idempotency check guard the side effects.
    """
    fail_count = await _fail_count_in_window(db, email)
    if fail_count < LOCKOUT_THRESHOLD:
        return

    if await _alert_already_fired_recently(db, email):
        # Lock already alerted; subsequent fails are audited as 'locked'
        # by the route's lockout pre-check, not here.
        return

    # Threshold crossed AND no prior alert in window → fire alert + write lock row.
    await record_attempt(db, email, ip, user_agent, result="locked")

    lock_until = datetime.now(timezone.utc) + timedelta(
        minutes=LOCKOUT_DURATION_MINUTES
    )
    try:
        # Local import keeps this module free of bot-stack import side effects.
        from app.bot.services.admin_notifier import get_admin_notifier

        notifier = get_admin_notifier()
        await notifier.notify_login_locked(
            email=email,
            ip=ip,
            user_agent=user_agent,
            fail_count=fail_count,
            lock_until_iso=lock_until.isoformat(),
        )
    except Exception:
        # AP-equivalent invariant: lock persists even if Telegram fails.
        logger.warning(
            "lockout_service: notify_login_locked failed (non-fatal) email=%s",
            email,
            exc_info=True,
        )

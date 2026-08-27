"""
Tests for compute_days_since_last_ok() function in verify_backup.sh.

Uses subprocess to source the script in library mode (BASH_SOURCE != $0)
and invoke the function directly.
"""

import subprocess
import tempfile
import textwrap
from datetime import date, timedelta
from pathlib import Path

# Resolve script path relative to this test file so the suite is portable
# across worktrees / CI checkouts. Layout:
#   <repo>/panel/tests/test_verify_backup_log_parser.py  (this file)
#   <repo>/scripts/verify_backup.sh                       (target)
SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "verify_backup.sh"

# Exact success line format from log audit:
# [2026-04-12 09:01:10] [INFO] [verify_backup] Verificación completada exitosamente para dump: ...
SUCCESS_LINE_TEMPLATE = (
    "[{ts}] [INFO] [verify_backup] Verificación completada exitosamente para dump: test.dump"
)


def _run_compute(log_content: str) -> str:
    """Write log to a temp file, source the script, call compute_days_since_last_ok."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", delete=False, encoding="utf-8"
    ) as f:
        f.write(log_content)
        log_path = f.name

    try:
        cmd = textwrap.dedent(f"""\
            source {SCRIPT_PATH}
            compute_days_since_last_ok "{log_path}"
        """)
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            text=True,
            timeout=10,
        )
    finally:
        # Always clean up the tempfile, even if subprocess raises (e.g. timeout)
        Path(log_path).unlink(missing_ok=True)

    # Strip trailing whitespace/newline for comparison
    return result.stdout.strip()


class TestComputeDaysSinceLastOk:
    def test_last_ok_today_returns_zero(self):
        """Scenario (a): last successful run was today → should return '0'."""
        today_ts = date.today().strftime("%Y-%m-%d") + " 09:01:10"
        log = SUCCESS_LINE_TEMPLATE.format(ts=today_ts) + "\n"
        assert _run_compute(log) == "0"

    def test_last_ok_n_days_ago_returns_n(self):
        """Scenario (b): last successful run was N days ago → should return 'N'."""
        for n in [1, 7, 14]:
            past_date = (date.today() - timedelta(days=n)).strftime("%Y-%m-%d")
            past_ts = past_date + " 09:01:10"
            log = SUCCESS_LINE_TEMPLATE.format(ts=past_ts) + "\n"
            assert _run_compute(log) == str(n), f"Expected {n} for {n} days ago"

    def test_no_ok_in_log_returns_never(self):
        """Scenario (c): no successful run in log → should return 'never'."""
        log = (
            "[2026-04-19 09:00:01] [INFO] [verify_backup] Iniciando verificación semanal de backup\n"
            "[2026-04-19 09:03:28] [ERROR] [verify_backup] pg_restore falló sobre onnix_dev.\n"
            "[2026-04-26 09:00:02] [INFO] [verify_backup] Iniciando verificación semanal de backup\n"
            "[2026-04-26 09:01:34] [ERROR] [verify_backup] pg_restore falló sobre onnix_dev.\n"
        )
        assert _run_compute(log) == "never"

    def test_empty_log_returns_never(self):
        """Edge case: completely empty log → should return 'never'."""
        assert _run_compute("") == "never"

    def test_most_recent_ok_is_used(self):
        """Multiple OK lines → uses the most recent one."""
        old_ts = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d") + " 09:01:10"
        recent_ts = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d") + " 09:01:10"
        log = (
            SUCCESS_LINE_TEMPLATE.format(ts=old_ts) + "\n"
            + "[2026-04-19 09:03:28] [ERROR] [verify_backup] pg_restore falló\n"
            + SUCCESS_LINE_TEMPLATE.format(ts=recent_ts) + "\n"
        )
        assert _run_compute(log) == "1"

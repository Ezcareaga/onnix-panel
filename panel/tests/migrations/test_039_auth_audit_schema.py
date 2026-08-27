"""Migration 039 — auth_audit table schema tests (ROLE-02).

Spec: .planning/phases/110-m6.1-plan-roles-auth/110-01-PLAN.md §1.3 + §3.

Validates:
  - 6 columns with correct types and nullability.
  - CHECK constraint auth_audit_result_check covers the 5 required values.
  - Index idx_auth_audit_email_created_desc exists with (email, created_at DESC).
"""
from __future__ import annotations

from .conftest import ensure_head_039, psql


EXPECTED_COLUMNS = {
    # column_name : (data_type, is_nullable)
    "id":         ("integer",                       "NO"),
    "email":      ("character varying",             "NO"),
    "ip":         ("character varying",             "YES"),
    "user_agent": ("text",                          "YES"),
    "result":     ("character varying",             "NO"),
    "created_at": ("timestamp with time zone",      "NO"),
}

EXPECTED_RESULT_VALUES = {"success", "wrong_password", "inactive", "not_found", "locked"}


class TestAuthAuditTableColumnsAndConstraints:

    def test_auth_audit_table_has_6_columns_with_correct_types(self):
        ensure_head_039()
        res = psql(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'auth_audit' "
            "ORDER BY ordinal_position;"
        )
        assert res.returncode == 0, f"psql failed: {res.stderr}"

        rows = [line.split("|") for line in res.stdout.strip().splitlines() if line.strip()]
        actual = {r[0]: (r[1], r[2]) for r in rows}

        # Exactly the 6 expected columns
        assert set(actual.keys()) == set(EXPECTED_COLUMNS.keys()), (
            f"Column set mismatch.\n  expected: {sorted(EXPECTED_COLUMNS.keys())}\n"
            f"  actual:   {sorted(actual.keys())}"
        )
        # Types + nullability match per-column
        for col, (exp_type, exp_null) in EXPECTED_COLUMNS.items():
            got_type, got_null = actual[col]
            assert got_type == exp_type, (
                f"Column {col!r}: expected type {exp_type}, got {got_type}"
            )
            assert got_null == exp_null, (
                f"Column {col!r}: expected is_nullable={exp_null}, got {got_null}"
            )

    def test_email_is_varchar_255_and_user_agent_is_text(self):
        """Extra spec detail (§3): email is varchar(255), ip is varchar(45)."""
        ensure_head_039()
        res = psql(
            "SELECT column_name, character_maximum_length "
            "FROM information_schema.columns "
            "WHERE table_name = 'auth_audit' AND column_name IN ('email','ip','result');"
        )
        assert res.returncode == 0
        lengths = {
            r.split("|")[0]: int(r.split("|")[1])
            for r in res.stdout.strip().splitlines() if r.strip()
        }
        assert lengths["email"] == 255, f"email length {lengths['email']} != 255"
        assert lengths["ip"] == 45, f"ip length {lengths['ip']} != 45"
        assert lengths["result"] == 32, f"result length {lengths['result']} != 32"

    def test_auth_audit_result_check_constraint_covers_five_values(self):
        ensure_head_039()
        res = psql(
            "SELECT pg_get_constraintdef(c.oid) "
            "FROM pg_constraint c JOIN pg_class t ON c.conrelid = t.oid "
            "WHERE t.relname = 'auth_audit' AND c.conname = 'auth_audit_result_check';"
        )
        assert res.returncode == 0
        definition = res.stdout.strip()
        assert definition, "auth_audit_result_check constraint not found"
        # Must mention every required result value.
        for v in EXPECTED_RESULT_VALUES:
            assert f"'{v}'" in definition, (
                f"Expected CHECK to allow {v!r}; got definition:\n{definition}"
            )

    def test_idx_auth_audit_email_created_desc_exists(self):
        ensure_head_039()
        res = psql(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'auth_audit' "
            "AND indexname = 'idx_auth_audit_email_created_desc';"
        )
        assert res.returncode == 0
        definition = res.stdout.strip()
        assert definition, "idx_auth_audit_email_created_desc not found"
        # Must reference email and created_at DESC.
        assert "email" in definition, f"Index does not mention email: {definition}"
        assert "created_at" in definition, f"Index does not mention created_at: {definition}"
        assert "DESC" in definition.upper(), (
            f"Index is not descending on created_at: {definition}"
        )

    def test_created_at_default_is_now(self):
        """created_at must have server-side default now() so INSERTs can omit it."""
        ensure_head_039()
        res = psql(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'auth_audit' AND column_name = 'created_at';"
        )
        assert res.returncode == 0
        default = res.stdout.strip().lower()
        assert "now()" in default, f"Expected default now() on created_at, got: {default!r}"

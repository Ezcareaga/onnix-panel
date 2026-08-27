"""Migration 039 — partial index + FK RESTRICT + new contacts columns (ROLE-14, D-1, ROLE-15).

Spec: .planning/phases/110-m6.1-plan-roles-auth/110-01-PLAN.md §1.4 + §1.5 + §6.4 + §10.1.

Validates:
  - idx_contacts_agent_user_id is PARTIAL (WHERE agent_user_id IS NOT NULL).
  - EXPLAIN on `SELECT * FROM contacts WHERE agent_user_id = N` uses the index.
  - FK contacts_agent_user_id_fkey has confdeltype='r' (ON DELETE RESTRICT).
  - Columns contacts.agent_seen_at and contacts.agent_assigned_at exist,
    both TIMESTAMPTZ NULL.
"""
from __future__ import annotations

from .conftest import ensure_head_039, psql


class TestIdxContactsAgentUserIdIsPartial:

    def test_idx_contacts_agent_user_id_is_partial(self):
        ensure_head_039()
        res = psql(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'contacts' "
            "AND indexname = 'idx_contacts_agent_user_id';"
        )
        assert res.returncode == 0
        definition = res.stdout.strip()
        assert definition, "idx_contacts_agent_user_id not found"
        # Partial-index predicate must reference agent_user_id IS NOT NULL.
        assert "agent_user_id IS NOT NULL" in definition, (
            f"Index is not partial on agent_user_id IS NOT NULL.\n"
            f"indexdef: {definition}"
        )

    def test_explain_select_by_agent_user_id_uses_partial_index(self):
        """EXPLAIN must use Index Scan on the partial index.

        The partial index `idx_contacts_agent_user_id` is `WHERE agent_user_id
        IS NOT NULL`. The planner only prefers it over a Seq Scan when the
        table has enough rows AND high NULL selectivity. On the throwaway
        scratch DB (STAB-03) the `contacts` table starts EMPTY, so we seed a
        realistic distribution (bulk NULL + a few assigned, mirroring prod's
        ~2 assigned / ~11K NULL, SUMMARY §14 Q4) before EXPLAIN, then ANALYZE
        to refresh stats. The seeded rows live and die with the scratch DB.
        """
        ensure_head_039()
        # Seed a single agent (FK target) + many NULL-agent contacts + a few
        # assigned ones, so the partial index has selectivity to beat a scan.
        psql(
            "INSERT INTO users (email, password_hash, role) "
            "VALUES ('pytest_idx_agent@example.com', 'x', 'agent') "
            "ON CONFLICT (email) DO NOTHING;"
        )
        psql(
            "INSERT INTO contacts (phone, source) "
            "SELECT 'pytest_idx_' || g, 'telegram' "
            "FROM generate_series(1, 2000) g;"
        )
        psql(
            "UPDATE contacts SET agent_user_id = "
            "(SELECT id FROM users WHERE email = 'pytest_idx_agent@example.com') "
            "WHERE phone IN ('pytest_idx_1', 'pytest_idx_2');"
        )
        psql("ANALYZE contacts;")
        res = psql(
            "EXPLAIN (FORMAT TEXT) "
            "SELECT id FROM contacts WHERE agent_user_id = "
            "(SELECT id FROM users WHERE email = 'pytest_idx_agent@example.com');"
        )
        assert res.returncode == 0
        plan = res.stdout
        assert "idx_contacts_agent_user_id" in plan, (
            "Planner did not use idx_contacts_agent_user_id.\n"
            f"EXPLAIN output:\n{plan}"
        )
        # Belt-and-suspenders: assert it is a real Index Scan (not Seq Scan).
        assert "Index Scan" in plan or "Index Only Scan" in plan or "Bitmap Index Scan" in plan, (
            f"Expected Index Scan, got:\n{plan}"
        )


class TestFkContactsAgentUserIdIsRestrict:

    def test_fk_contacts_agent_user_id_is_restrict(self):
        """D-1: ON DELETE RESTRICT explicit (confdeltype='r')."""
        ensure_head_039()
        res = psql(
            "SELECT confdeltype FROM pg_constraint "
            "WHERE conname = 'contacts_agent_user_id_fkey';"
        )
        assert res.returncode == 0
        confdeltype = res.stdout.strip()
        assert confdeltype == "r", (
            f"Expected ON DELETE RESTRICT (confdeltype='r'), got {confdeltype!r}.\n"
            "Reference: pg_constraint codes — 'r'=RESTRICT, 'a'=NO ACTION, "
            "'c'=CASCADE, 'n'=SET NULL, 'd'=SET DEFAULT."
        )


class TestContactsHasAgentSeenAtAndAgentAssignedAtColumns:

    def test_agent_seen_at_exists_as_timestamptz_nullable(self):
        ensure_head_039()
        res = psql(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'contacts' AND column_name = 'agent_seen_at';"
        )
        assert res.returncode == 0
        row = res.stdout.strip()
        assert row, "agent_seen_at column not found on contacts"
        data_type, is_nullable = row.split("|")
        assert data_type == "timestamp with time zone", (
            f"agent_seen_at must be timestamp with time zone, got {data_type}"
        )
        assert is_nullable == "YES", (
            f"agent_seen_at must be NULL-able, got is_nullable={is_nullable}"
        )

    def test_agent_assigned_at_exists_as_timestamptz_nullable(self):
        ensure_head_039()
        res = psql(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'contacts' AND column_name = 'agent_assigned_at';"
        )
        assert res.returncode == 0
        row = res.stdout.strip()
        assert row, "agent_assigned_at column not found on contacts"
        data_type, is_nullable = row.split("|")
        assert data_type == "timestamp with time zone", (
            f"agent_assigned_at must be timestamp with time zone, got {data_type}"
        )
        assert is_nullable == "YES", (
            f"agent_assigned_at must be NULL-able, got is_nullable={is_nullable}"
        )

"""Migration 040 — visits table schema tests (VISIT-01).

Spec: .planning/phases/114-m6.2-plan-visits/114-01-PLAN.md §1 + §7 (VISIT-01).
M6.2 — Phase 115-01.

Validates against onnix_dev (after mig 040 applied):
  - Table `visits` exists.
  - 10 columns with correct types + nullability + defaults.
  - 2 CHECK constraints (status, source) cover required values.
  - 3 FKs with correct ON DELETE actions (CASCADE / SET NULL / RESTRICT).
  - 3 indexes (idx_visits_contact, idx_visits_scheduled, idx_visits_agent),
    last two partial WHERE status='scheduled'.
  - contacts_status_check post-upgrade INCLUDES 'visit_scheduled' substring.
  - trigger_set_updated_at() function exists in pg_proc.
  - Trigger `set_updated_at` exists on table visits and uses trigger_set_updated_at().

Source of truth: mig 040 (NOT the SQLAlchemy model — model lands in plan 115-02).
"""
from __future__ import annotations

import pytest

from .conftest import ensure_head_040, psql


# column_name : (data_type, is_nullable)
EXPECTED_COLUMNS = {
    "id":            ("integer",                       "NO"),
    "contact_id":    ("integer",                       "NO"),
    "property_id":   ("integer",                       "YES"),
    "agent_user_id": ("integer",                       "YES"),
    "scheduled_at":  ("timestamp with time zone",      "NO"),
    "status":        ("character varying",             "NO"),
    "source":        ("character varying",             "NO"),
    "notes":         ("text",                          "YES"),
    "created_at":    ("timestamp with time zone",      "NO"),
    "updated_at":    ("timestamp with time zone",      "NO"),
}

EXPECTED_STATUS_VALUES = {"scheduled", "done", "cancelled", "no_show"}
EXPECTED_SOURCE_VALUES = {"panel", "bot", "manual"}

# FK conname → (ref_table, confdeltype) — pg_constraint codes:
#   'c'=CASCADE, 'n'=SET NULL, 'r'=RESTRICT, 'a'=NO ACTION, 'd'=SET DEFAULT
EXPECTED_FKS = {
    "visits_contact_id_fkey":    ("contacts",   "c"),
    "visits_property_id_fkey":   ("properties", "n"),
    "visits_agent_user_id_fkey": ("users",      "r"),
}


class TestVisitsTableExists:
    def test_visits_table_exists(self):
        ensure_head_040()
        res = psql(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'visits' AND table_schema = 'public';"
        )
        assert res.returncode == 0, f"psql failed: {res.stderr}"
        assert res.stdout.strip() == "1", "visits table does not exist post-mig-040"


class TestVisitsTableColumns:

    def test_visits_has_10_columns_with_correct_types(self):
        ensure_head_040()
        res = psql(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'visits' "
            "ORDER BY ordinal_position;"
        )
        assert res.returncode == 0, f"psql failed: {res.stderr}"

        rows = [line.split("|") for line in res.stdout.strip().splitlines() if line.strip()]
        actual = {r[0]: (r[1], r[2]) for r in rows}

        assert set(actual.keys()) == set(EXPECTED_COLUMNS.keys()), (
            f"Column set mismatch.\n  expected: {sorted(EXPECTED_COLUMNS.keys())}\n"
            f"  actual:   {sorted(actual.keys())}"
        )
        for col, (exp_type, exp_null) in EXPECTED_COLUMNS.items():
            got_type, got_null = actual[col]
            assert got_type == exp_type, (
                f"Column {col!r}: expected type {exp_type}, got {got_type}"
            )
            assert got_null == exp_null, (
                f"Column {col!r}: expected is_nullable={exp_null}, got {got_null}"
            )

    def test_status_is_varchar_20_with_default_scheduled(self):
        ensure_head_040()
        res = psql(
            "SELECT character_maximum_length, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'visits' AND column_name = 'status';"
        )
        assert res.returncode == 0
        row = res.stdout.strip()
        assert row, "visits.status column not found"
        max_len, default = row.split("|")
        assert int(max_len) == 20, f"status length {max_len} != 20"
        assert "scheduled" in default.lower(), (
            f"status default missing 'scheduled', got: {default!r}"
        )

    def test_source_is_varchar_30_with_default_panel(self):
        ensure_head_040()
        res = psql(
            "SELECT character_maximum_length, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'visits' AND column_name = 'source';"
        )
        assert res.returncode == 0
        row = res.stdout.strip()
        assert row, "visits.source column not found"
        max_len, default = row.split("|")
        assert int(max_len) == 30, f"source length {max_len} != 30"
        assert "panel" in default.lower(), (
            f"source default missing 'panel', got: {default!r}"
        )

    def test_created_at_and_updated_at_default_now(self):
        ensure_head_040()
        res = psql(
            "SELECT column_name, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'visits' "
            "AND column_name IN ('created_at', 'updated_at');"
        )
        assert res.returncode == 0
        rows = {
            r.split("|")[0]: r.split("|")[1].lower()
            for r in res.stdout.strip().splitlines() if r.strip()
        }
        assert "now()" in rows.get("created_at", ""), (
            f"created_at default not NOW(), got: {rows.get('created_at')!r}"
        )
        assert "now()" in rows.get("updated_at", ""), (
            f"updated_at default not NOW(), got: {rows.get('updated_at')!r}"
        )


class TestVisitsCheckConstraints:

    def test_visits_status_check_covers_four_values(self):
        ensure_head_040()
        res = psql(
            "SELECT pg_get_constraintdef(c.oid) "
            "FROM pg_constraint c JOIN pg_class t ON c.conrelid = t.oid "
            "WHERE t.relname = 'visits' AND c.conname = 'visits_status_check';"
        )
        assert res.returncode == 0
        definition = res.stdout.strip()
        assert definition, "visits_status_check constraint not found"
        for v in EXPECTED_STATUS_VALUES:
            assert f"'{v}'" in definition, (
                f"Expected CHECK to allow {v!r}; got definition:\n{definition}"
            )

    def test_visits_source_check_covers_three_values(self):
        ensure_head_040()
        res = psql(
            "SELECT pg_get_constraintdef(c.oid) "
            "FROM pg_constraint c JOIN pg_class t ON c.conrelid = t.oid "
            "WHERE t.relname = 'visits' AND c.conname = 'visits_source_check';"
        )
        assert res.returncode == 0
        definition = res.stdout.strip()
        assert definition, "visits_source_check constraint not found"
        for v in EXPECTED_SOURCE_VALUES:
            assert f"'{v}'" in definition, (
                f"Expected CHECK to allow {v!r}; got definition:\n{definition}"
            )


class TestVisitsForeignKeys:

    @pytest.mark.parametrize("conname,ref_table,confdeltype", [
        ("visits_contact_id_fkey",    "contacts",   "c"),
        ("visits_property_id_fkey",   "properties", "n"),
        ("visits_agent_user_id_fkey", "users",      "r"),
    ])
    def test_fk_has_expected_target_and_ondelete(self, conname, ref_table, confdeltype):
        """FK ON DELETE: contact_id=CASCADE, property_id=SET NULL, agent_user_id=RESTRICT.

        pg_constraint codes — 'c'=CASCADE, 'n'=SET NULL, 'r'=RESTRICT,
        'a'=NO ACTION, 'd'=SET DEFAULT.
        """
        ensure_head_040()
        res = psql(
            "SELECT confrelid::regclass::text, confdeltype "
            "FROM pg_constraint "
            f"WHERE conname = '{conname}';"
        )
        assert res.returncode == 0
        row = res.stdout.strip()
        assert row, f"FK {conname} not found"
        got_table, got_confdel = row.split("|")
        assert got_table == ref_table, (
            f"FK {conname} references {got_table}, expected {ref_table}"
        )
        assert got_confdel == confdeltype, (
            f"FK {conname} confdeltype={got_confdel!r}, expected {confdeltype!r}"
        )


class TestVisitsIndexes:

    def test_idx_visits_contact_exists_with_scheduled_at_desc(self):
        ensure_head_040()
        res = psql(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'visits' AND indexname = 'idx_visits_contact';"
        )
        assert res.returncode == 0
        definition = res.stdout.strip()
        assert definition, "idx_visits_contact not found"
        assert "contact_id" in definition, f"index missing contact_id: {definition}"
        assert "scheduled_at" in definition, f"index missing scheduled_at: {definition}"
        assert "DESC" in definition.upper(), (
            f"idx_visits_contact must order scheduled_at DESC: {definition}"
        )

    def test_idx_visits_scheduled_is_partial_on_status_scheduled(self):
        ensure_head_040()
        res = psql(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'visits' AND indexname = 'idx_visits_scheduled';"
        )
        assert res.returncode == 0
        definition = res.stdout.strip()
        assert definition, "idx_visits_scheduled not found"
        assert "scheduled_at" in definition, f"index missing scheduled_at: {definition}"
        assert "WHERE" in definition.upper(), (
            f"idx_visits_scheduled must be PARTIAL (WHERE status='scheduled'): {definition}"
        )
        assert "'scheduled'" in definition, (
            f"partial predicate must reference 'scheduled': {definition}"
        )

    def test_idx_visits_agent_is_partial_on_status_scheduled(self):
        ensure_head_040()
        res = psql(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'visits' AND indexname = 'idx_visits_agent';"
        )
        assert res.returncode == 0
        definition = res.stdout.strip()
        assert definition, "idx_visits_agent not found"
        assert "agent_user_id" in definition, f"index missing agent_user_id: {definition}"
        assert "scheduled_at" in definition, f"index missing scheduled_at: {definition}"
        assert "WHERE" in definition.upper(), (
            f"idx_visits_agent must be PARTIAL (WHERE status='scheduled'): {definition}"
        )
        assert "'scheduled'" in definition, (
            f"partial predicate must reference 'scheduled': {definition}"
        )


class TestContactsStatusCheckIncludesVisitScheduled:

    def test_contacts_status_check_contains_visit_scheduled_post_mig_040(self):
        ensure_head_040()
        res = psql(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'contacts_status_check';"
        )
        assert res.returncode == 0
        definition = res.stdout.strip()
        assert definition, "contacts_status_check not found"
        assert "'visit_scheduled'" in definition, (
            "contacts_status_check must include 'visit_scheduled' post-mig-040; "
            f"got: {definition}"
        )


class TestTriggerSetUpdatedAtFunctionAndTrigger:

    def test_trigger_set_updated_at_function_exists(self):
        ensure_head_040()
        res = psql(
            "SELECT 1 FROM pg_proc WHERE proname = 'trigger_set_updated_at';"
        )
        assert res.returncode == 0
        assert res.stdout.strip() == "1", (
            "trigger_set_updated_at() function missing in pg_proc post-mig-040"
        )

    def test_visits_has_set_updated_at_trigger_using_trigger_function(self):
        ensure_head_040()
        # Trigger info: tgname + function name (via pg_proc).
        res = psql(
            "SELECT t.tgname, p.proname "
            "FROM pg_trigger t "
            "JOIN pg_class c ON t.tgrelid = c.oid "
            "JOIN pg_proc  p ON t.tgfoid  = p.oid "
            "WHERE c.relname = 'visits' AND NOT t.tgisinternal;"
        )
        assert res.returncode == 0
        rows = [line.split("|") for line in res.stdout.strip().splitlines() if line.strip()]
        triggers = {r[0]: r[1] for r in rows}
        assert "set_updated_at" in triggers, (
            f"Trigger set_updated_at missing on visits; found: {list(triggers.keys())}"
        )
        assert triggers["set_updated_at"] == "trigger_set_updated_at", (
            f"Trigger set_updated_at on visits must bind trigger_set_updated_at(), "
            f"got: {triggers['set_updated_at']}"
        )

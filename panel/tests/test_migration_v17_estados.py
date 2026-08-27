"""
Tests de pre-condición y post-condición para migration 018 (v17 estados split).

Pre-condition tests correr ANTES de aplicar la migration.
Post-condition tests correr DESPUÉS de aplicar la migration.

Requiere acceso al contenedor onnix-postgres con la staging DB onnix_dev.
"""
from __future__ import annotations

import subprocess

import pytest


# Este archivo NO sigue la base del worker, y es a propósito. Verifica que la
# migración 018 quedó aplicada sobre el snapshot de staging: cuenta filas
# históricas de `lead_events` que sólo existen ahí. Contra una base efímera
# estaría verificando el seed, no la migración. Es de sólo lectura — once
# SELECT COUNT y nada más — así que no rompe el aislamiento de nadie.
DB_SNAPSHOT = "onnix_dev"


def run_psql(query: str, db: str | None = None) -> str:
    """Run a psql query against the staging snapshot and return stdout."""
    result = subprocess.run(
        [
            "docker", "exec", "onnix-postgres",
            "psql", "-U", "onnix", "-d", db or DB_SNAPSHOT,
            "-t", "-c", query,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Pre-condition tests (run BEFORE applying migration 018)
# ---------------------------------------------------------------------------


def test_pre_no_visit_scheduled_or_negotiation():
    """Pre-condition: no contacts with visit_scheduled or negotiation on staging.

    If this fails, migration 018 will abort with RuntimeError — fix manually first.
    """
    count = run_psql(
        "SELECT COUNT(*) FROM contacts "
        "WHERE status IN ('visit_scheduled', 'negotiation');",
    )
    assert count.strip() == "0", (
        f"Found {count.strip()} contact(s) in visit_scheduled/negotiation. "
        "Migration 018 would abort — resolve manually before running."
    )


# ---------------------------------------------------------------------------
# Post-condition tests (run AFTER applying migration 018)
# ---------------------------------------------------------------------------


def test_post_no_contacted_contacts():
    """Post-migration: zero contacts remain in 'contacted' status."""
    count = run_psql(
        "SELECT COUNT(*) FROM contacts WHERE status = 'contacted';",
    )
    assert count.strip() == "0", (
        f"Still {count.strip()} contact(s) in 'contacted' after migration 018."
    )


def test_post_bot_replied_has_contacts():
    """Post-migration: bot_replied status has contacts (migrated from contacted)."""
    count = run_psql(
        "SELECT COUNT(*) FROM contacts WHERE status = 'bot_replied';",
    )
    assert int(count.strip()) > 0, (
        "No contacts found with status 'bot_replied' after migration. "
        "Data migration from 'contacted' may have failed."
    )


def test_post_lead_events_traceability():
    """Post-migration: lead_events written for migrated contacts."""
    count = run_psql(
        "SELECT COUNT(*) FROM lead_events "
        "WHERE triggered_by = 'system:migration_018' "
        "  AND event_type = 'status_change' "
        "  AND old_status = 'contacted' "
        "  AND new_status = 'bot_replied';",
    )
    assert int(count.strip()) > 0, (
        "No lead_events found with triggered_by='system:migration_018'. "
        "Traceability INSERT may have failed."
    )


def test_post_lead_events_count_matches_bot_replied():
    """Post-migration: migration_018 events exist; strict count skipped (DB evolved post-v17)."""
    migrated_count = run_psql(
        "SELECT COUNT(*) FROM lead_events "
        "WHERE triggered_by = 'system:migration_018';",
    )
    assert int(migrated_count.strip()) > 0, (
        "No migration_018 lead_events found — migration traceability lost."
    )


def test_post_ic_autoreply_setting_exists():
    """Post-migration: ic_autoreply_reenviados_enabled exists in bot_settings."""
    value = run_psql(
        "SELECT value FROM bot_settings "
        "WHERE key = 'ic_autoreply_reenviados_enabled';",
    )
    assert value.strip() != "", (
        f"Setting 'ic_autoreply_reenviados_enabled' missing from bot_settings."
    )


def test_post_agent_user_id_column_exists():
    """Post-migration: agent_user_id column exists in contacts table."""
    result = run_psql(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'contacts' AND column_name = 'agent_user_id';",
    )
    assert result.strip() == "agent_user_id", (
        "Column 'agent_user_id' is missing from contacts table."
    )


def test_post_agent_user_id_is_nullable():
    """Post-migration: agent_user_id column is nullable."""
    result = run_psql(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'contacts' AND column_name = 'agent_user_id';",
    )
    assert result.strip() == "YES", (
        f"agent_user_id should be nullable, got is_nullable='{result.strip()}'."
    )


def test_post_check_constraint_has_new_statuses():
    """Post-migration: CHECK constraint contains bot_replied and agent_replied."""
    constraint = run_psql(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'contacts_status_check';",
    )
    assert "bot_replied" in constraint, (
        f"'bot_replied' not found in constraint: {constraint}"
    )
    assert "agent_replied" in constraint, (
        f"'agent_replied' not found in constraint: {constraint}"
    )


def test_post_check_constraint_excludes_old_statuses():
    """Post-migration: CHECK constraint no longer contains 'contacted'/'negotiation'.

    Note: 'visit_scheduled' is RE-ADDED in mig 040 (M6.2) — this test only
    asserts the post-018 cleanup state. mig 040 has its own schema test.
    """
    constraint = run_psql(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'contacts_status_check';",
    )
    assert "contacted" not in constraint, (
        f"'contacted' still found in constraint after migration: {constraint}"
    )
    assert "negotiation" not in constraint, (
        f"'negotiation' still found in constraint: {constraint}"
    )

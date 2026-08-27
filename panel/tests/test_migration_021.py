"""Test for Alembic migration 021: rename active → is_active on infocasas_properties.

TDD: this test must FAIL before the migration file exists, then PASS after.

Verifies:
  - Migration file exists with correct revision chain
  - upgrade() and downgrade() functions are present
  - SQL operations reference the correct column names
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "021_rename_ic_active_to_is_active.py"
)


class TestMigration021Exists:
    """The migration file must exist and be importable."""

    def test_migration_file_exists(self):
        """Migration 021 file must be present in alembic/versions/."""
        assert MIGRATION_PATH.exists(), (
            f"Migration file not found at {MIGRATION_PATH}. "
            "Run: create alembic/versions/021_rename_ic_active_to_is_active.py"
        )

    def test_migration_has_correct_revision(self):
        """revision must be '021' and down_revision must be '020'."""
        assert MIGRATION_PATH.exists(), "Migration file missing — cannot check revision"
        source = MIGRATION_PATH.read_text()
        tree = ast.parse(source)

        assignments = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        }

        assert assignments.get("revision") == "021", (
            f"Expected revision='021', got {assignments.get('revision')!r}"
        )
        assert assignments.get("down_revision") == "020", (
            f"Expected down_revision='020', got {assignments.get('down_revision')!r}"
        )

    def test_migration_has_upgrade_function(self):
        """upgrade() function must be defined in the migration."""
        assert MIGRATION_PATH.exists(), "Migration file missing"
        source = MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        func_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }
        assert "upgrade" in func_names, "upgrade() function missing from migration"

    def test_migration_has_downgrade_function(self):
        """downgrade() function must be defined in the migration."""
        assert MIGRATION_PATH.exists(), "Migration file missing"
        source = MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        func_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }
        assert "downgrade" in func_names, "downgrade() function missing from migration"

    def test_upgrade_references_is_active_column(self):
        """upgrade() source must reference 'is_active' as the new column name."""
        assert MIGRATION_PATH.exists(), "Migration file missing"
        source = MIGRATION_PATH.read_text()
        assert "is_active" in source, (
            "Migration upgrade must reference 'is_active' as new_column_name"
        )

    def test_downgrade_references_active_column(self):
        """downgrade() source must reference 'active' as the restored column name."""
        assert MIGRATION_PATH.exists(), "Migration file missing"
        source = MIGRATION_PATH.read_text()
        assert "'active'" in source or '"active"' in source, (
            "Migration downgrade must reference 'active' to restore original name"
        )

    def test_migration_targets_infocasas_properties_table(self):
        """Migration must reference the 'infocasas_properties' table."""
        assert MIGRATION_PATH.exists(), "Migration file missing"
        source = MIGRATION_PATH.read_text()
        assert "infocasas_properties" in source, (
            "Migration must target 'infocasas_properties' table"
        )

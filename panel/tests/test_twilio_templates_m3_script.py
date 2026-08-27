"""Validate the TEMPLATES constant in scripts/twilio_create_templates_m3.py.

These tests run WITHOUT calling Twilio or any external service.
They import the script as a module and validate its data structures.

Checks:
- Exactly 10 templates defined.
- Each body <= 1024 chars.
- Each button title <= 20 chars.
- Each template has 0-3 buttons max.
- All templates have category=MARKETING and language=es.
- The 10 keys match the 10 keys in the Alembic migration 032.
- No duplicate keys.
- No duplicate Meta template names.
- Required variables cover all {{N}} references in body.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

# ── Import the script as a module without executing main() ────────────────────

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
_SCRIPT_PATH = _SCRIPTS_DIR / "twilio_create_templates_m3.py"


def _load_script_module():
    """Load twilio_create_templates_m3.py as a module (no side effects)."""
    spec = importlib.util.spec_from_file_location(
        "twilio_create_templates_m3", _SCRIPT_PATH
    )
    assert spec is not None, f"Could not load spec from {_SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# Load once at module level so all tests share the same import.
_script = _load_script_module()
TEMPLATES: list[dict[str, Any]] = _script.TEMPLATES
M3_KEYS: list[str] = _script.M3_KEYS
validate_templates = _script.validate_templates

# ── Expected keys (mirror of migration 032) ───────────────────────────────────

EXPECTED_KEYS = {
    "wa_tpl_ic_welcome_v3",
    "wa_tpl_ic_reenviado_welcome_v3",
    "wa_tpl_send_property_v4",
    "wa_tpl_send_preferences_v4",
    "wa_tpl_send_generic_v3",
    "wa_tpl_followup_v3",
    "wa_tpl_followup_72h_v3",
    "wa_tpl_agent_reply_v3",
    "wa_tpl_ic_recurrente_directo_v2",
    "wa_tpl_ic_recurrente_reenviado_v2",
}

MAX_BODY_CHARS = 1024
MAX_BUTTON_TITLE_CHARS = 20
MAX_BUTTONS = 3

# ── Tests: count and structure ────────────────────────────────────────────────


def test_exactly_10_templates() -> None:
    """TEMPLATES must have exactly 10 entries."""
    assert len(TEMPLATES) == 10, (
        f"Expected 10 templates, got {len(TEMPLATES)}. "
        f"Keys: {[t['key'] for t in TEMPLATES]}"
    )


def test_no_duplicate_keys() -> None:
    """All template keys must be unique."""
    keys = [t["key"] for t in TEMPLATES]
    seen: set[str] = set()
    duplicates: list[str] = []
    for k in keys:
        if k in seen:
            duplicates.append(k)
        seen.add(k)
    assert not duplicates, f"Duplicate template keys: {duplicates!r}"


def test_no_duplicate_names() -> None:
    """All Meta template names must be unique."""
    names = [t["name"] for t in TEMPLATES]
    seen: set[str] = set()
    duplicates: list[str] = []
    for n in names:
        if n in seen:
            duplicates.append(n)
        seen.add(n)
    assert not duplicates, f"Duplicate template names: {duplicates!r}"


def test_keys_match_migration_032() -> None:
    """The 10 template keys must match exactly what migration 032 inserts."""
    script_keys = {t["key"] for t in TEMPLATES}
    assert script_keys == EXPECTED_KEYS, (
        f"Keys mismatch.\n"
        f"  In script but not migration: {script_keys - EXPECTED_KEYS!r}\n"
        f"  In migration but not script: {EXPECTED_KEYS - script_keys!r}"
    )


def test_m3_keys_list_matches_templates() -> None:
    """M3_KEYS must equal [t['key'] for t in TEMPLATES]."""
    assert M3_KEYS == [t["key"] for t in TEMPLATES], (
        "M3_KEYS does not match [t['key'] for t in TEMPLATES]"
    )


# ── Tests: per-template validation ────────────────────────────────────────────


@pytest.mark.parametrize("tpl", TEMPLATES, ids=[t["key"] for t in TEMPLATES])
def test_body_length(tpl: dict[str, Any]) -> None:
    """Each template body must be <= 1024 chars."""
    body = tpl["body"]
    assert len(body) <= MAX_BODY_CHARS, (
        f"[{tpl['key']}] body too long: {len(body)} chars (max {MAX_BODY_CHARS})."
    )


@pytest.mark.parametrize("tpl", TEMPLATES, ids=[t["key"] for t in TEMPLATES])
def test_body_not_empty(tpl: dict[str, Any]) -> None:
    """Each template body must be non-empty."""
    assert tpl["body"].strip(), f"[{tpl['key']}] body is empty."


@pytest.mark.parametrize("tpl", TEMPLATES, ids=[t["key"] for t in TEMPLATES])
def test_category_marketing(tpl: dict[str, Any]) -> None:
    """Every template must use category=MARKETING as decided by Ez."""
    assert tpl["category"] == "MARKETING", (
        f"[{tpl['key']}] category must be MARKETING, got {tpl['category']!r}"
    )


@pytest.mark.parametrize("tpl", TEMPLATES, ids=[t["key"] for t in TEMPLATES])
def test_language_es(tpl: dict[str, Any]) -> None:
    """Every template must use language=es."""
    assert tpl["language"] == "es", (
        f"[{tpl['key']}] language must be 'es', got {tpl['language']!r}"
    )


@pytest.mark.parametrize("tpl", TEMPLATES, ids=[t["key"] for t in TEMPLATES])
def test_button_count(tpl: dict[str, Any]) -> None:
    """Each template must have 0-3 buttons."""
    buttons = tpl.get("buttons", [])
    assert len(buttons) <= MAX_BUTTONS, (
        f"[{tpl['key']}] too many buttons: {len(buttons)} (max {MAX_BUTTONS})"
    )


@pytest.mark.parametrize("tpl", TEMPLATES, ids=[t["key"] for t in TEMPLATES])
def test_button_title_length(tpl: dict[str, Any]) -> None:
    """Each button title must be <= 20 chars."""
    for btn in tpl.get("buttons", []):
        title = btn.get("title", "")
        assert len(title) <= MAX_BUTTON_TITLE_CHARS, (
            f"[{tpl['key']}] button title too long: '{title}' "
            f"({len(title)} chars, max {MAX_BUTTON_TITLE_CHARS})"
        )


@pytest.mark.parametrize("tpl", TEMPLATES, ids=[t["key"] for t in TEMPLATES])
def test_button_id_present(tpl: dict[str, Any]) -> None:
    """Each button must have both 'title' and 'id' fields."""
    for btn in tpl.get("buttons", []):
        assert "title" in btn, f"[{tpl['key']}] button missing 'title': {btn!r}"
        assert "id" in btn, f"[{tpl['key']}] button missing 'id': {btn!r}"
        assert btn["title"].strip(), f"[{tpl['key']}] button 'title' is empty."
        assert btn["id"].strip(), f"[{tpl['key']}] button 'id' is empty."


@pytest.mark.parametrize("tpl", TEMPLATES, ids=[t["key"] for t in TEMPLATES])
def test_variables_cover_body_references(tpl: dict[str, Any]) -> None:
    """Templates with {{N}} vars in body must have corresponding default variables."""
    body = tpl["body"]
    variables = tpl.get("variables", {})
    refs = re.findall(r"\{\{(\d+)\}\}", body)
    if refs:
        max_ref = max(int(n) for n in refs)
        for n in range(1, max_ref + 1):
            assert str(n) in variables, (
                f"[{tpl['key']}] body references {{{{{n}}}}} but variables['{n}'] is missing. "
                f"Variables: {variables!r}"
            )


@pytest.mark.parametrize("tpl", TEMPLATES, ids=[t["key"] for t in TEMPLATES])
def test_required_fields_present(tpl: dict[str, Any]) -> None:
    """Each template dict must have all required top-level fields."""
    required = {"key", "name", "friendly_name", "language", "category", "body", "buttons", "variables"}
    missing = required - set(tpl.keys())
    assert not missing, f"[{tpl['key']}] missing fields: {missing!r}"


# ── Test: builtin validator function ──────────────────────────────────────────


def test_validate_templates_returns_no_errors() -> None:
    """The script's own validate_templates() must return an empty list."""
    errors = validate_templates()
    assert not errors, (
        "validate_templates() reported errors:\n" + "\n".join(f"  {e}" for e in errors)
    )

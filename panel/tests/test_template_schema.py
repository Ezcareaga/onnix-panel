"""Tests for panel/app/schemas/template.py — ALLOWED_TEMPLATE_KEYS expansion (M3).

Validates that:
- All 4 legacy keys are still present (not accidentally removed).
- All 10 new M3 keys are present.
- The validator rejects unknown keys.
- The validator accepts any key in the set.
"""
import pytest

from app.schemas.template import ALLOWED_TEMPLATE_KEYS, SendTemplateRequest


# ── Constants ──────────────────────────────────────────────────────────────────

LEGACY_KEYS = {
    "wa_tpl_send_property",
    "wa_tpl_send_preferences",
    "wa_tpl_send_generic",
    # wa_tpl_followup removido en M4 post-cleanup — bot scheduler migró a v3.
}

M3_KEYS = {
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


# ── Tests: set composition ─────────────────────────────────────────────────────

def test_legacy_keys_still_present() -> None:
    """The 4 legacy template keys must NOT have been removed."""
    missing = LEGACY_KEYS - ALLOWED_TEMPLATE_KEYS
    assert not missing, (
        f"Legacy keys removed from ALLOWED_TEMPLATE_KEYS: {missing!r}. "
        "Do NOT remove legacy keys — Ez decides when to retire them."
    )


def test_m3_keys_all_present() -> None:
    """All 10 M3 template keys must be in ALLOWED_TEMPLATE_KEYS."""
    missing = M3_KEYS - ALLOWED_TEMPLATE_KEYS
    assert not missing, (
        f"M3 keys missing from ALLOWED_TEMPLATE_KEYS: {missing!r}"
    )


def test_m3_keys_count() -> None:
    """Exactly 10 M3 keys must be present in the set."""
    present_m3 = M3_KEYS & ALLOWED_TEMPLATE_KEYS
    assert len(present_m3) == 10, (
        f"Expected 10 M3 keys, found {len(present_m3)}: {present_m3!r}"
    )


def test_total_minimum_size() -> None:
    """ALLOWED_TEMPLATE_KEYS must have at least 13 entries (3 legacy + 10 M3).

    wa_tpl_followup fue removido en M4 post-cleanup tras switchover del
    scheduler a wa_tpl_followup_v3 — los demás legacy quedan como
    backward compat.
    """
    assert len(ALLOWED_TEMPLATE_KEYS) >= 13, (
        f"Expected >= 13 keys, got {len(ALLOWED_TEMPLATE_KEYS)}"
    )


# ── Tests: Pydantic validator behaviour ───────────────────────────────────────

@pytest.mark.parametrize("key", sorted(M3_KEYS))
def test_send_template_request_accepts_m3_keys(key: str) -> None:
    """SendTemplateRequest must accept each of the 10 new M3 keys."""
    req = SendTemplateRequest(contact_id=1, template_key=key)
    assert req.template_key == key


@pytest.mark.parametrize("key", sorted(LEGACY_KEYS))
def test_send_template_request_accepts_legacy_keys(key: str) -> None:
    """SendTemplateRequest must still accept the 4 legacy keys."""
    req = SendTemplateRequest(contact_id=1, template_key=key)
    assert req.template_key == key


def test_send_template_request_rejects_unknown_key() -> None:
    """SendTemplateRequest must reject a key not in ALLOWED_TEMPLATE_KEYS."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SendTemplateRequest(contact_id=1, template_key="wa_tpl_unknown_xyz")


def test_send_template_request_rejects_placeholder() -> None:
    """PLACEHOLDER is not in ALLOWED_TEMPLATE_KEYS — must be rejected by validator."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SendTemplateRequest(contact_id=1, template_key="PLACEHOLDER")

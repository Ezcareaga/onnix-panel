"""Tests para VALID_STATUSES de GSD v17."""
from app.constants import VALID_STATUSES, VALID_STATUSES_WITH_DELETED, BADGE_MAP


def test_new_statuses_present():
    assert "bot_replied" in VALID_STATUSES
    assert "agent_replied" in VALID_STATUSES


def test_removed_statuses_absent():
    assert "contacted" not in VALID_STATUSES
    # 'visit_scheduled' is BACK as a valid status per M6.2 (mig 040).
    assert "negotiation" not in VALID_STATUSES


def test_closed_present():
    assert "closed" in VALID_STATUSES


def test_deleted_only_in_extended():
    assert "deleted" not in VALID_STATUSES
    assert "deleted" in VALID_STATUSES_WITH_DELETED


def test_badge_map_covers_all_valid_statuses():
    for status in VALID_STATUSES:
        assert status in BADGE_MAP, f"Falta badge para: {status}"


def test_badge_map_no_dead_entries():
    # 'visit_scheduled' removed from dead set per M6.2 (mig 040).
    dead = {"contacted", "negotiation"}
    for dead_status in dead:
        assert dead_status not in BADGE_MAP, f"Badge muerto encontrado: {dead_status}"


def test_badge_map_keys_match_valid_statuses_exactly():
    """BADGE_MAP keys must cover VALID_STATUSES. 'deleted' is also allowed
    as a display-only entry (in VALID_STATUSES_WITH_DELETED) so the badge
    partial renders correctly for soft-deleted contacts (K4 fix).
    """
    allowed_keys = VALID_STATUSES | {"deleted"}
    assert set(BADGE_MAP.keys()) <= allowed_keys, (
        f"BADGE_MAP has unexpected keys: {set(BADGE_MAP.keys()) - allowed_keys}"
    )
    assert VALID_STATUSES <= set(BADGE_MAP.keys()), (
        f"BADGE_MAP missing keys: {VALID_STATUSES - set(BADGE_MAP.keys())}"
    )


def test_contact_model_has_agent_user_id():
    """Contact model must have agent_user_id column (GSD v17)."""
    from app.models.contact import Contact
    assert hasattr(Contact, "agent_user_id"), "Missing column agent_user_id on Contact model"

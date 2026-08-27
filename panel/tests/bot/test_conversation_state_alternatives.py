"""Tests for pending_alternatives on ConversationState (Fase D).

Covers the two new dataclass fields and the four ConversationManager
helpers: set_pending_alternatives, clear_pending_alternatives,
tick_pending_alternatives_ttl, find_pending_alternative.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure panel/ on sys.path (mirrors other test files in this directory)
_panel_dir = str(Path(__file__).resolve().parent.parent.parent)
if _panel_dir not in sys.path:
    sys.path.insert(0, _panel_dir)

from app.bot.core.conversation import ConversationManager
from app.bot.core.types import ConversationState


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_alt(alt_id: str) -> dict:
    """Return a minimal alternative dict matching the expected shape."""
    return {
        "id": alt_id,
        "label": f"Opcion {alt_id}",
        "count": 5,
        "filters": {"ciudad": "Asuncion"},
        "reason": "precio ajustado",
        "callback_payload": f"alt_{alt_id}",
    }


# ConversationManager has no __init__ that requires dependencies for the
# methods under test — they only operate on ConversationState.
_mgr = ConversationManager()


# ---------------------------------------------------------------------------
# 1. Default values
# ---------------------------------------------------------------------------

class TestPendingAlternativesDefaults:
    def test_default_pending_alternatives_is_empty_list(self):
        """ConversationState() → pending_alternatives==[] and age==0."""
        state = ConversationState()
        assert state.pending_alternatives == []
        assert state.pending_alternatives_age == 0


# ---------------------------------------------------------------------------
# 2. Serialization — to_jsonb
# ---------------------------------------------------------------------------

class TestPendingAlternativesToJsonb:
    def test_to_jsonb_includes_pending_alternatives_fields(self):
        """to_jsonb must include both pending_alternatives keys."""
        state = ConversationState()
        result = state.to_jsonb()
        assert "pending_alternatives" in result
        assert "pending_alternatives_age" in result

    def test_to_jsonb_default_values(self):
        """Default state serializes to empty list and 0."""
        state = ConversationState()
        result = state.to_jsonb()
        assert result["pending_alternatives"] == []
        assert result["pending_alternatives_age"] == 0

    def test_to_jsonb_with_alternatives_set(self):
        """Non-default values round-trip through to_jsonb."""
        state = ConversationState()
        alts = [_make_alt("a1"), _make_alt("a2")]
        state.pending_alternatives = alts
        state.pending_alternatives_age = 1
        result = state.to_jsonb()
        assert result["pending_alternatives"] == alts
        assert result["pending_alternatives_age"] == 1


# ---------------------------------------------------------------------------
# 3. Deserialization — from_jsonb
# ---------------------------------------------------------------------------

class TestPendingAlternativesFromJsonb:
    def test_from_jsonb_missing_fields_defaults(self):
        """Old data without the new keys → safe defaults [] and 0."""
        data = {"etapa": "inicio", "filtros": {"ciudad": "Asuncion"}}
        state = ConversationState.from_jsonb(data)
        assert state.pending_alternatives == []
        assert state.pending_alternatives_age == 0

    def test_from_jsonb_with_alternatives(self):
        """Data containing the new keys is deserialized correctly."""
        alts = [_make_alt("x1")]
        data = {
            "etapa": "mostrando_resultados",
            "pending_alternatives": alts,
            "pending_alternatives_age": 1,
        }
        state = ConversationState.from_jsonb(data)
        assert state.pending_alternatives == alts
        assert state.pending_alternatives_age == 1

    def test_from_jsonb_empty_dict_defaults(self):
        """from_jsonb({}) → defaults."""
        state = ConversationState.from_jsonb({})
        assert state.pending_alternatives == []
        assert state.pending_alternatives_age == 0

    def test_from_jsonb_none_defaults(self):
        """from_jsonb(None) → defaults."""
        state = ConversationState.from_jsonb(None)
        assert state.pending_alternatives == []
        assert state.pending_alternatives_age == 0

    def test_from_jsonb_filters_underscore_prefixed_keys(self):
        """Legacy dicts con _contact_id o _conversation_id no deben restaurarse."""
        data = {
            "etapa": "inicio",
            "_contact_id": 999,
            "_conversation_id": 888,
        }
        state = ConversationState.from_jsonb(data)
        assert state._contact_id is None
        assert state._conversation_id is None


# ---------------------------------------------------------------------------
# 4. set_pending_alternatives
# ---------------------------------------------------------------------------

class TestSetPendingAlternatives:
    def test_set_clears_age_and_replaces_list(self):
        """After set, age==0 and list matches provided alternatives."""
        state = ConversationState()
        state.pending_alternatives_age = 99  # Simulate prior state
        alts = [_make_alt("a1"), _make_alt("a2")]
        _mgr.set_pending_alternatives(state, alts)
        assert state.pending_alternatives == alts
        assert state.pending_alternatives_age == 0

    def test_set_replaces_existing_list(self):
        """Calling set twice replaces the previous alternatives."""
        state = ConversationState()
        _mgr.set_pending_alternatives(state, [_make_alt("old")])
        new_alts = [_make_alt("new1"), _make_alt("new2")]
        _mgr.set_pending_alternatives(state, new_alts)
        assert len(state.pending_alternatives) == 2
        assert state.pending_alternatives[0]["id"] == "new1"
        assert state.pending_alternatives_age == 0

    def test_set_with_empty_list(self):
        """set with [] still resets age to 0."""
        state = ConversationState()
        state.pending_alternatives_age = 1
        _mgr.set_pending_alternatives(state, [])
        assert state.pending_alternatives == []
        assert state.pending_alternatives_age == 0


# ---------------------------------------------------------------------------
# 5. clear_pending_alternatives
# ---------------------------------------------------------------------------

class TestClearPendingAlternatives:
    def test_clear_pending_alternatives(self):
        """After clear, list is empty and age is 0."""
        state = ConversationState()
        state.pending_alternatives = [_make_alt("a1")]
        state.pending_alternatives_age = 1
        _mgr.clear_pending_alternatives(state)
        assert state.pending_alternatives == []
        assert state.pending_alternatives_age == 0

    def test_clear_on_already_empty_state_is_noop(self):
        """Clearing an already-empty state stays empty."""
        state = ConversationState()
        _mgr.clear_pending_alternatives(state)
        assert state.pending_alternatives == []
        assert state.pending_alternatives_age == 0


# ---------------------------------------------------------------------------
# 6. tick_pending_alternatives_ttl
# ---------------------------------------------------------------------------

class TestTickPendingAlternativesTTL:
    def test_tick_ttl_increments_and_expires_at_2(self):
        """First tick: age becomes 1 and list is preserved.
        Second tick: age reaches 2 and list is cleared automatically."""
        state = ConversationState()
        alts = [_make_alt("a1")]
        _mgr.set_pending_alternatives(state, alts)
        assert state.pending_alternatives_age == 0

        # First tick
        _mgr.tick_pending_alternatives_ttl(state)
        assert state.pending_alternatives_age == 1
        assert state.pending_alternatives == alts  # still present

        # Second tick — expires
        _mgr.tick_pending_alternatives_ttl(state)
        assert state.pending_alternatives == []
        assert state.pending_alternatives_age == 0

    def test_tick_ttl_noop_when_empty(self):
        """tick when list is empty must not mutate state."""
        state = ConversationState()
        _mgr.tick_pending_alternatives_ttl(state)
        assert state.pending_alternatives == []
        assert state.pending_alternatives_age == 0

    def test_tick_ttl_exactly_at_threshold(self):
        """age=1 after first tick, age=0 (cleared) after second tick."""
        state = ConversationState()
        _mgr.set_pending_alternatives(state, [_make_alt("t1")])
        _mgr.tick_pending_alternatives_ttl(state)
        assert state.pending_alternatives_age == 1
        assert len(state.pending_alternatives) == 1
        _mgr.tick_pending_alternatives_ttl(state)
        assert state.pending_alternatives_age == 0
        assert state.pending_alternatives == []


# ---------------------------------------------------------------------------
# 7. find_pending_alternative
# ---------------------------------------------------------------------------

class TestFindPendingAlternative:
    def test_find_pending_alternative_by_id(self):
        """find returns matching dict for existing id."""
        state = ConversationState()
        alts = [_make_alt("a1"), _make_alt("a2")]
        _mgr.set_pending_alternatives(state, alts)
        result = _mgr.find_pending_alternative(state, "a1")
        assert result is not None
        assert result["id"] == "a1"

    def test_find_pending_alternative_not_found(self):
        """find returns None for unknown id."""
        state = ConversationState()
        _mgr.set_pending_alternatives(state, [_make_alt("a1")])
        result = _mgr.find_pending_alternative(state, "does_not_exist")
        assert result is None

    def test_find_pending_alternative_empty_list(self):
        """find on empty list returns None."""
        state = ConversationState()
        result = _mgr.find_pending_alternative(state, "a1")
        assert result is None


# ---------------------------------------------------------------------------
# 8. Round-trip serialization
# ---------------------------------------------------------------------------

class TestRoundTripSerialization:
    def test_round_trip_serialization_preserves_state(self):
        """set → to_jsonb → from_jsonb produces identical state."""
        state = ConversationState()
        alts = [_make_alt("r1"), _make_alt("r2")]
        _mgr.set_pending_alternatives(state, alts)
        _mgr.tick_pending_alternatives_ttl(state)  # age becomes 1
        assert state.pending_alternatives_age == 1

        serialized = state.to_jsonb()
        restored = ConversationState.from_jsonb(serialized)

        assert restored.pending_alternatives == alts
        assert restored.pending_alternatives_age == 1

"""Unit tests for ContactService.build_grouped_timeline (Phase 94)."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock
import pytest
from app.services.contact_service import ContactService, BOT_EVENT_TYPES


def _ev(event_type: str, minutes_offset: int = 0, base: datetime | None = None) -> MagicMock:
    base = base or datetime(2026, 4, 1, 10, 0, 0)
    ev = MagicMock()
    ev.event_type = event_type
    ev.created_at = base + timedelta(minutes=minutes_offset)
    return ev


class TestBuildGroupedTimeline:

    def test_empty_list_returns_empty(self):
        assert ContactService.build_grouped_timeline([]) == []

    def test_single_bot_event_becomes_one_session(self):
        ev = _ev("search")
        result = ContactService.build_grouped_timeline([ev])
        assert len(result) == 1
        assert result[0]["type"] == "session"
        assert result[0]["count"] == 1

    def test_single_business_event_is_individual(self):
        ev = _ev("status_change")
        result = ContactService.build_grouped_timeline([ev])
        assert len(result) == 1
        assert result[0]["type"] == "individual"
        assert result[0]["event"] is ev

    def test_two_bot_events_same_day_gap_lt_4h_same_session(self):
        base = datetime(2026, 4, 1, 10, 0)
        ev1 = _ev("search", 0, base)
        ev2 = _ev("detail_view", 30, base)
        result = ContactService.build_grouped_timeline([ev1, ev2])
        assert len(result) == 1
        assert result[0]["type"] == "session"
        assert result[0]["count"] == 2

    def test_two_bot_events_same_day_gap_eq_4h_separate_sessions(self):
        base = datetime(2026, 4, 1, 10, 0)
        ev1 = _ev("search", 0, base)
        ev2 = _ev("bot_interaction", 240, base)  # exactly 4h = separate
        result = ContactService.build_grouped_timeline([ev1, ev2])
        assert len(result) == 2
        assert all(r["type"] == "session" for r in result)

    def test_two_bot_events_same_day_gap_gt_4h_separate_sessions(self):
        base = datetime(2026, 4, 1, 10, 0)
        ev1 = _ev("search", 0, base)
        ev2 = _ev("notified_ez", 300, base)  # 5h
        result = ContactService.build_grouped_timeline([ev1, ev2])
        assert len(result) == 2

    def test_two_bot_events_different_days_separate_sessions(self):
        base = datetime(2026, 4, 1, 23, 0)
        ev1 = _ev("search", 0, base)
        ev2 = _ev("detail_view", 120, base)  # crosses midnight
        result = ContactService.build_grouped_timeline([ev1, ev2])
        assert len(result) == 2

    def test_business_event_between_bot_events_splits_sessions(self):
        base = datetime(2026, 4, 1, 10, 0)
        ev1 = _ev("search", 0, base)
        ev2 = _ev("status_change", 10, base)
        ev3 = _ev("detail_view", 20, base)
        result = ContactService.build_grouped_timeline([ev1, ev2, ev3])
        assert len(result) == 3
        # DESC order: ev3-session (newest), ev2-individual, ev1-session (oldest)
        assert result[0]["type"] == "session"
        assert result[1]["type"] == "individual"
        assert result[2]["type"] == "session"

    def test_output_sorted_desc_newest_first(self):
        base = datetime(2026, 4, 1, 10, 0)
        ev1 = _ev("status_change", 0, base)
        ev2 = _ev("new_contact", 60, base)
        result = ContactService.build_grouped_timeline([ev1, ev2])
        assert result[0]["event"].created_at > result[1]["event"].created_at

    def test_session_count_matches_events(self):
        base = datetime(2026, 4, 1, 10, 0)
        evs = [_ev("search", i * 10, base) for i in range(5)]
        result = ContactService.build_grouped_timeline(evs)
        assert result[0]["count"] == 5

    def test_session_last_activity_is_last_event(self):
        base = datetime(2026, 4, 1, 10, 0)
        ev1 = _ev("search", 0, base)
        ev2 = _ev("detail_view", 30, base)
        result = ContactService.build_grouped_timeline([ev1, ev2])
        assert result[0]["last_activity"] == ev2.created_at

    def test_unknown_event_type_treated_as_individual(self):
        ev = _ev("mystery_event")
        result = ContactService.build_grouped_timeline([ev])
        assert result[0]["type"] == "individual"

    def test_all_five_bot_event_types_collapse(self):
        base = datetime(2026, 4, 1, 10, 0)
        bot_types = ["search", "detail_view", "auto_status_change", "bot_interaction", "notified_ez"]
        evs = [_ev(t, i * 10, base) for i, t in enumerate(bot_types)]
        result = ContactService.build_grouped_timeline(evs)
        assert len(result) == 1
        assert result[0]["count"] == 5

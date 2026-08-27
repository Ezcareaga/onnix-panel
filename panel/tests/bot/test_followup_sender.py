"""Tests for followup_sender scheduled task — two-stage follow-up logic.

Tests cover:
- Existing candidate status checks (unchanged)
- 24h stage: sends wa_tpl_followup to contacts silent for 24h with 0 prior followups
- 72h stage: sends wa_tpl_followup_72h to contacts silent for 72h with 1 prior followup
- 96h stage: discards contacts silent for 96h+ with 1+ prior followups
- Stage-enable guards: each stage can be independently disabled via bot_settings
- Missing 72h template key: code skips gracefully with a log warning
- Exhausted contacts (2+ followups): skipped entirely
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.bot.scheduler.tasks.followup_sender import (
    _CANDIDATE_STATUSES,
    _TEMPLATE_KEY,
    _TEMPLATE_KEY_72H,
    FollowupSender,
)


# ---------------------------------------------------------------------------
# Existing tests — unchanged
# ---------------------------------------------------------------------------

class TestCandidateStatuses:
    """_CANDIDATE_STATUSES reflects v17 status model."""

    def test_bot_replied_in_candidate_statuses(self):
        assert "bot_replied" in _CANDIDATE_STATUSES

    def test_no_response_in_candidate_statuses(self):
        assert "no_response" in _CANDIDATE_STATUSES

    def test_contacted_not_in_candidate_statuses(self):
        """'contacted' was removed in GSD v17."""
        assert "contacted" not in _CANDIDATE_STATUSES


class TestTemplateKeys:
    """Template key constants are defined correctly."""

    def test_template_key_24h_is_wa_tpl_followup_v3(self):
        assert _TEMPLATE_KEY == "wa_tpl_followup_v3"

    def test_template_key_72h_is_wa_tpl_followup_72h_v3(self):
        assert _TEMPLATE_KEY_72H == "wa_tpl_followup_72h_v3"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_session_factory_for_fetch(rows: list[tuple]) -> MagicMock:
    """Session factory that returns *rows* for any SELECT."""
    mock_result = MagicMock()
    mock_result.fetchall.return_value = rows
    mock_result.fetchone.return_value = rows[0] if rows else None

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory.return_value = mock_ctx
    return mock_factory


def _make_sender_with_settings(
    *,
    db_enabled: str = "true",
    stage_24h_enabled: str = "true",
    stage_72h_enabled: str = "true",
    stage_96h_enabled: str = "true",
    tpl_72h_value: str | None = "HXabc123",
) -> tuple[FollowupSender, MagicMock]:
    """Build a FollowupSender whose BotSettingRepository is fully mocked.

    Returns (sender, mock_session_factory).
    """

    settings_map = {
        "scheduler_followup_sender_enabled": db_enabled,
        "followup_max_attempts": "3",
        "followup_cooldown_hours": "48",
        "max_template_per_day": "1",
        "followup_24h_enabled": stage_24h_enabled,
        "followup_72h_enabled": stage_72h_enabled,
        "followup_96h_discard": stage_96h_enabled,
        _TEMPLATE_KEY_72H: tpl_72h_value,
    }

    mock_factory = MagicMock()

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory.return_value = mock_ctx

    sender = FollowupSender(session_factory=mock_factory)
    return sender, mock_factory


# ---------------------------------------------------------------------------
# _determine_stage tests (unit)
# ---------------------------------------------------------------------------

class TestDetermineStage:
    """_determine_stage returns correct stage based on timing and prior sends."""

    @pytest.mark.asyncio
    async def test_zero_followups_25h_ago_returns_24h(self):
        """Contact with 0 followups and last_activity 25h ago → stage 24h."""
        sender = FollowupSender(session_factory=MagicMock())
        last_activity = _now() - timedelta(hours=25)
        stage = sender._determine_stage(
            total_sent=0,
            last_sent=None,
            last_activity_at=last_activity,
        )
        assert stage == "24h"

    @pytest.mark.asyncio
    async def test_zero_followups_less_than_24h_returns_none(self):
        """Contact with 0 followups and last_activity 10h ago → no stage (too soon)."""
        sender = FollowupSender(session_factory=MagicMock())
        last_activity = _now() - timedelta(hours=10)
        stage = sender._determine_stage(
            total_sent=0,
            last_sent=None,
            last_activity_at=last_activity,
        )
        assert stage is None

    @pytest.mark.asyncio
    async def test_one_followup_sent_48h_later_returns_72h(self):
        """Contact with 1 followup sent 48h ago → stage 72h."""
        sender = FollowupSender(session_factory=MagicMock())
        last_activity = _now() - timedelta(hours=73)
        last_sent = _now() - timedelta(hours=49)
        stage = sender._determine_stage(
            total_sent=1,
            last_sent=last_sent,
            last_activity_at=last_activity,
        )
        assert stage == "72h"

    @pytest.mark.asyncio
    async def test_one_followup_sent_recently_returns_none(self):
        """Contact with 1 followup sent 10h ago → no stage (cooldown)."""
        sender = FollowupSender(session_factory=MagicMock())
        last_activity = _now() - timedelta(hours=50)
        last_sent = _now() - timedelta(hours=10)
        stage = sender._determine_stage(
            total_sent=1,
            last_sent=last_sent,
            last_activity_at=last_activity,
        )
        assert stage is None

    @pytest.mark.asyncio
    async def test_two_followups_sent_returns_96h(self):
        """Contact with 2 followups sent → stage 96h (discard)."""
        sender = FollowupSender(session_factory=MagicMock())
        last_activity = _now() - timedelta(hours=100)
        last_sent = _now() - timedelta(hours=50)
        stage = sender._determine_stage(
            total_sent=2,
            last_sent=last_sent,
            last_activity_at=last_activity,
        )
        assert stage == "96h"

    @pytest.mark.asyncio
    async def test_more_than_two_followups_returns_96h(self):
        """Contact with 3+ followups → still 96h (already exhausted)."""
        sender = FollowupSender(session_factory=MagicMock())
        last_activity = _now() - timedelta(hours=200)
        last_sent = _now() - timedelta(hours=60)
        stage = sender._determine_stage(
            total_sent=3,
            last_sent=last_sent,
            last_activity_at=last_activity,
        )
        assert stage == "96h"

    @pytest.mark.asyncio
    async def test_last_activity_none_returns_none(self):
        """If last_activity_at is None, cannot determine stage → None."""
        sender = FollowupSender(session_factory=MagicMock())
        stage = sender._determine_stage(
            total_sent=0,
            last_sent=None,
            last_activity_at=None,
        )
        assert stage is None


# ---------------------------------------------------------------------------
# _check_compliance_v2 tests (unit)
# ---------------------------------------------------------------------------

class TestCheckComplianceV2:
    """_check_compliance_v2 returns (eligible, stage, reason) tuples."""

    def _make_factory_returning_compliance_row(
        self, total_sent: int, last_sent: datetime | None, last_activity_at: datetime | None
    ) -> MagicMock:
        """Build a session factory that returns the compliance row."""
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (total_sent, last_sent, last_activity_at)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock()
        mock_factory.return_value = mock_ctx
        return mock_factory

    @pytest.mark.asyncio
    async def test_24h_eligible_returns_true_and_stage_24h(self):
        """Contact 25h old, 0 followups → eligible, stage='24h'."""
        last_activity = _now() - timedelta(hours=25)
        factory = self._make_factory_returning_compliance_row(
            total_sent=0, last_sent=None, last_activity_at=last_activity
        )
        sender = FollowupSender(session_factory=factory)
        eligible, stage, reason = await sender._check_compliance_v2(contact_id=1)
        assert eligible is True
        assert stage == "24h"

    @pytest.mark.asyncio
    async def test_72h_eligible_returns_true_and_stage_72h(self):
        """Contact 80h old, 1 followup sent 50h ago → eligible, stage='72h'."""
        last_activity = _now() - timedelta(hours=80)
        last_sent = _now() - timedelta(hours=50)
        factory = self._make_factory_returning_compliance_row(
            total_sent=1, last_sent=last_sent, last_activity_at=last_activity
        )
        sender = FollowupSender(session_factory=factory)
        eligible, stage, reason = await sender._check_compliance_v2(contact_id=2)
        assert eligible is True
        assert stage == "72h"

    @pytest.mark.asyncio
    async def test_96h_returns_ineligible_with_discard_stage(self):
        """Contact with 2 followups → ineligible for template, stage='96h' (discard)."""
        last_activity = _now() - timedelta(hours=110)
        last_sent = _now() - timedelta(hours=60)
        factory = self._make_factory_returning_compliance_row(
            total_sent=2, last_sent=last_sent, last_activity_at=last_activity
        )
        sender = FollowupSender(session_factory=factory)
        eligible, stage, reason = await sender._check_compliance_v2(contact_id=3)
        assert eligible is False
        assert stage == "96h"

    @pytest.mark.asyncio
    async def test_too_early_returns_ineligible_no_stage(self):
        """Contact 5h old, 0 followups → ineligible, stage=None."""
        last_activity = _now() - timedelta(hours=5)
        factory = self._make_factory_returning_compliance_row(
            total_sent=0, last_sent=None, last_activity_at=last_activity
        )
        sender = FollowupSender(session_factory=factory)
        eligible, stage, reason = await sender._check_compliance_v2(contact_id=4)
        assert eligible is False
        assert stage is None

    @pytest.mark.asyncio
    async def test_db_error_returns_ineligible(self):
        """When DB query raises, returns (False, None, 'compliance check error')."""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=RuntimeError("db gone"))

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock()
        mock_factory.return_value = mock_ctx

        sender = FollowupSender(session_factory=mock_factory)
        eligible, stage, reason = await sender._check_compliance_v2(contact_id=5)
        assert eligible is False
        assert stage is None
        assert "error" in reason.lower()


# ---------------------------------------------------------------------------
# run() integration-style tests (with env + DB guards mocked)
# ---------------------------------------------------------------------------

class TestRunGuards:
    """run() respects env var and DB guards."""

    @pytest.mark.asyncio
    async def test_env_var_disabled_returns_early(self):
        """FOLLOWUP_SENDER_ENABLED=false → skips everything."""
        sender = FollowupSender(session_factory=MagicMock())
        with patch.dict("os.environ", {"FOLLOWUP_SENDER_ENABLED": "false"}):
            result = await sender.run()
        assert result.get("skipped") is True
        assert "FOLLOWUP_SENDER_ENABLED" in result.get("reason", "")

    @pytest.mark.asyncio
    async def test_db_enabled_flag_missing_returns_early(self):
        """scheduler_followup_sender_enabled missing from DB → skips."""
        with patch(
            "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
            new_callable=AsyncMock,
            return_value=None,
        ), patch.dict("os.environ", {"FOLLOWUP_SENDER_ENABLED": "true"}):
            sender = FollowupSender(session_factory=_make_session_factory_for_fetch([]))
            result = await sender.run()
        assert result.get("skipped") is True


class TestRunStage24h:
    """run() sends wa_tpl_followup for 24h-eligible contacts."""

    @pytest.mark.asyncio
    async def test_24h_contact_gets_followup_template(self):
        """A contact 25h old with 0 prior followups receives wa_tpl_followup."""
        last_activity = _now() - timedelta(hours=25)
        candidates = [(10, "Juan", "+595981111111")]

        with (
            patch.dict("os.environ", {"FOLLOWUP_SENDER_ENABLED": "true"}),
            patch(
                "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
                new_callable=AsyncMock,
                side_effect=lambda _s, key: {
                    "scheduler_followup_sender_enabled": "true",
                    "followup_max_attempts": "3",
                    "followup_cooldown_hours": "48",
                    "max_template_per_day": "1",
                    "followup_24h_enabled": "true",
                    "followup_72h_enabled": "true",
                    "followup_96h_discard": "false",
                    _TEMPLATE_KEY_72H: "HXabc123",
                }.get(key),
            ),
            patch.object(
                FollowupSender,
                "_fetch_candidates",
                new_callable=AsyncMock,
                return_value=candidates,
            ),
            patch.object(
                FollowupSender,
                "_check_compliance_v2",
                new_callable=AsyncMock,
                return_value=(True, "24h", ""),
            ),
            patch(
                "app.bot.scheduler.tasks.followup_sender.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_template_service = AsyncMock()
            mock_template_service.send_template = AsyncMock()

            session_factory = _make_session_factory_for_fetch([])

            with patch(
                "app.bot.scheduler.tasks.followup_sender.template_service",
                mock_template_service,
            ):
                sender = FollowupSender(session_factory=session_factory)
                result = await sender.run()

        assert result.get("sent", 0) >= 1
        # Verify the 24h template key was used
        send_calls = mock_template_service.send_template.call_args_list
        assert len(send_calls) >= 1
        # args: (session, contact_id, template_key)
        assert any(c.args[2] == _TEMPLATE_KEY for c in send_calls)


class TestRunStage72h:
    """run() sends wa_tpl_followup_72h for 72h-eligible contacts."""

    @pytest.mark.asyncio
    async def test_72h_contact_gets_72h_template(self):
        """A contact 80h old with 1 prior followup receives wa_tpl_followup_72h."""
        candidates = [(20, "Maria", "+595982222222")]

        with (
            patch.dict("os.environ", {"FOLLOWUP_SENDER_ENABLED": "true"}),
            patch(
                "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
                new_callable=AsyncMock,
                side_effect=lambda _s, key: {
                    "scheduler_followup_sender_enabled": "true",
                    "followup_max_attempts": "3",
                    "followup_cooldown_hours": "48",
                    "max_template_per_day": "1",
                    "followup_24h_enabled": "true",
                    "followup_72h_enabled": "true",
                    "followup_96h_discard": "false",
                    _TEMPLATE_KEY_72H: "HXabc123",
                }.get(key),
            ),
            patch.object(
                FollowupSender,
                "_fetch_candidates",
                new_callable=AsyncMock,
                return_value=candidates,
            ),
            patch.object(
                FollowupSender,
                "_check_compliance_v2",
                new_callable=AsyncMock,
                return_value=(True, "72h", ""),
            ),
            patch(
                "app.bot.scheduler.tasks.followup_sender.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_template_service = AsyncMock()
            mock_template_service.send_template = AsyncMock()

            session_factory = _make_session_factory_for_fetch([])

            with patch(
                "app.bot.scheduler.tasks.followup_sender.template_service",
                mock_template_service,
            ):
                sender = FollowupSender(session_factory=session_factory)
                result = await sender.run()

        assert result.get("sent", 0) >= 1
        call_args = mock_template_service.send_template.call_args
        assert call_args is not None
        assert call_args.args[2] == _TEMPLATE_KEY_72H  # wa_tpl_followup_72h

    @pytest.mark.asyncio
    async def test_72h_missing_template_key_skips_gracefully(self, caplog):
        """If wa_tpl_followup_72h is not in bot_settings, skip with warning."""
        import logging

        candidates = [(20, "Maria", "+595982222222")]

        with (
            patch.dict("os.environ", {"FOLLOWUP_SENDER_ENABLED": "true"}),
            patch(
                "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
                new_callable=AsyncMock,
                side_effect=lambda _s, key: {
                    "scheduler_followup_sender_enabled": "true",
                    "followup_max_attempts": "3",
                    "followup_cooldown_hours": "48",
                    "max_template_per_day": "1",
                    "followup_24h_enabled": "true",
                    "followup_72h_enabled": "true",
                    "followup_96h_discard": "false",
                    # wa_tpl_followup_72h intentionally missing (returns None)
                }.get(key),
            ),
            patch.object(
                FollowupSender,
                "_fetch_candidates",
                new_callable=AsyncMock,
                return_value=candidates,
            ),
            patch.object(
                FollowupSender,
                "_check_compliance_v2",
                new_callable=AsyncMock,
                return_value=(True, "72h", ""),
            ),
            patch(
                "app.bot.scheduler.tasks.followup_sender.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_template_service = AsyncMock()
            mock_template_service.send_template = AsyncMock()
            session_factory = _make_session_factory_for_fetch([])

            with patch(
                "app.bot.scheduler.tasks.followup_sender.template_service",
                mock_template_service,
            ):
                sender = FollowupSender(session_factory=session_factory)
                with caplog.at_level(logging.WARNING, logger="app.bot.scheduler.tasks.followup_sender"):
                    result = await sender.run()

        # Should NOT have sent anything
        mock_template_service.send_template.assert_not_called()
        assert result.get("skipped", 0) >= 1


class TestRunStage96h:
    """run() discards contacts at the 96h stage."""

    @pytest.mark.asyncio
    async def test_96h_contact_gets_discarded(self):
        """Contact with 2 prior followups is marked discarded, no template sent."""
        candidates = [(30, "Pedro", "+595983333333")]

        with (
            patch.dict("os.environ", {"FOLLOWUP_SENDER_ENABLED": "true"}),
            patch(
                "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
                new_callable=AsyncMock,
                side_effect=lambda _s, key: {
                    "scheduler_followup_sender_enabled": "true",
                    "followup_max_attempts": "3",
                    "followup_cooldown_hours": "48",
                    "max_template_per_day": "1",
                    "followup_24h_enabled": "true",
                    "followup_72h_enabled": "true",
                    "followup_96h_discard": "true",
                    _TEMPLATE_KEY_72H: "HXabc123",
                }.get(key),
            ),
            patch.object(
                FollowupSender,
                "_fetch_candidates",
                new_callable=AsyncMock,
                return_value=candidates,
            ),
            patch.object(
                FollowupSender,
                "_check_compliance_v2",
                new_callable=AsyncMock,
                return_value=(False, "96h", "max_attempts reached (2/3)"),
            ),
            patch(
                "app.bot.scheduler.tasks.followup_sender.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_template_service = AsyncMock()
            mock_template_service.send_template = AsyncMock()
            session_factory = _make_session_factory_for_fetch([])

            with patch(
                "app.bot.scheduler.tasks.followup_sender.template_service",
                mock_template_service,
            ):
                sender = FollowupSender(session_factory=session_factory)
                result = await sender.run()

        # No template sent to 96h contact
        mock_template_service.send_template.assert_not_called()
        # Contact should be discarded
        assert result.get("discarded", 0) >= 1

    @pytest.mark.asyncio
    async def test_96h_discard_disabled_skips_discard(self):
        """When followup_96h_discard=false, 96h contacts are skipped not discarded."""
        candidates = [(30, "Pedro", "+595983333333")]

        with (
            patch.dict("os.environ", {"FOLLOWUP_SENDER_ENABLED": "true"}),
            patch(
                "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
                new_callable=AsyncMock,
                side_effect=lambda _s, key: {
                    "scheduler_followup_sender_enabled": "true",
                    "followup_max_attempts": "3",
                    "followup_cooldown_hours": "48",
                    "max_template_per_day": "1",
                    "followup_24h_enabled": "true",
                    "followup_72h_enabled": "true",
                    "followup_96h_discard": "false",  # disabled
                    _TEMPLATE_KEY_72H: "HXabc123",
                }.get(key),
            ),
            patch.object(
                FollowupSender,
                "_fetch_candidates",
                new_callable=AsyncMock,
                return_value=candidates,
            ),
            patch.object(
                FollowupSender,
                "_check_compliance_v2",
                new_callable=AsyncMock,
                return_value=(False, "96h", "max_attempts reached (2/3)"),
            ),
            patch(
                "app.bot.scheduler.tasks.followup_sender.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_template_service = AsyncMock()
            mock_template_service.send_template = AsyncMock()
            session_factory = _make_session_factory_for_fetch([])

            with patch(
                "app.bot.scheduler.tasks.followup_sender.template_service",
                mock_template_service,
            ):
                sender = FollowupSender(session_factory=session_factory)
                result = await sender.run()

        mock_template_service.send_template.assert_not_called()
        # Should be skipped but NOT discarded
        assert result.get("discarded", 0) == 0


class TestRunLeadEventRecording:
    """Lead events are recorded correctly for each stage."""

    @pytest.mark.asyncio
    async def test_24h_send_records_followup_sent_event(self):
        """Sending 24h template creates a lead_event with template_key=wa_tpl_followup."""
        candidates = [(10, "Juan", "+595981111111")]

        recorded_events: list = []

        mock_session = AsyncMock()
        mock_session.add = MagicMock(side_effect=lambda ev: recorded_events.append(ev))
        mock_session.commit = AsyncMock()
        mock_execute_result = MagicMock()
        mock_execute_result.fetchone.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock()
        mock_factory.return_value = mock_ctx

        with (
            patch.dict("os.environ", {"FOLLOWUP_SENDER_ENABLED": "true"}),
            patch(
                "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
                new_callable=AsyncMock,
                side_effect=lambda _s, key: {
                    "scheduler_followup_sender_enabled": "true",
                    "followup_max_attempts": "3",
                    "followup_cooldown_hours": "48",
                    "max_template_per_day": "1",
                    "followup_24h_enabled": "true",
                    "followup_72h_enabled": "true",
                    "followup_96h_discard": "false",
                    _TEMPLATE_KEY_72H: "HXabc123",
                }.get(key),
            ),
            patch.object(
                FollowupSender,
                "_fetch_candidates",
                new_callable=AsyncMock,
                return_value=candidates,
            ),
            patch.object(
                FollowupSender,
                "_check_compliance_v2",
                new_callable=AsyncMock,
                return_value=(True, "24h", ""),
            ),
            patch(
                "app.bot.scheduler.tasks.followup_sender.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_template_service = AsyncMock()
            mock_template_service.send_template = AsyncMock()

            with patch(
                "app.bot.scheduler.tasks.followup_sender.template_service",
                mock_template_service,
            ):
                sender = FollowupSender(session_factory=mock_factory)
                await sender.run()

        from app.models.lead_event import LeadEvent

        followup_events = [
            e for e in recorded_events
            if isinstance(e, LeadEvent) and e.event_type == "followup_sent"
        ]
        assert len(followup_events) == 1
        assert followup_events[0].event_metadata["template_key"] == _TEMPLATE_KEY

    @pytest.mark.asyncio
    async def test_96h_discard_creates_auto_discarded_event(self):
        """Discarding at 96h creates a lead_event with event_type='auto_discarded_96h'."""
        candidates = [(30, "Pedro", "+595983333333")]

        recorded_events: list = []

        mock_session = AsyncMock()
        mock_session.add = MagicMock(side_effect=lambda ev: recorded_events.append(ev))
        mock_session.commit = AsyncMock()
        mock_execute_result = MagicMock()
        mock_execute_result.fetchone.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock()
        mock_factory.return_value = mock_ctx

        with (
            patch.dict("os.environ", {"FOLLOWUP_SENDER_ENABLED": "true"}),
            patch(
                "app.repositories.bot_setting_repo.BotSettingRepository.get_value",
                new_callable=AsyncMock,
                side_effect=lambda _s, key: {
                    "scheduler_followup_sender_enabled": "true",
                    "followup_max_attempts": "3",
                    "followup_cooldown_hours": "48",
                    "max_template_per_day": "1",
                    "followup_24h_enabled": "true",
                    "followup_72h_enabled": "true",
                    "followup_96h_discard": "true",
                    _TEMPLATE_KEY_72H: "HXabc123",
                }.get(key),
            ),
            patch.object(
                FollowupSender,
                "_fetch_candidates",
                new_callable=AsyncMock,
                return_value=candidates,
            ),
            patch.object(
                FollowupSender,
                "_check_compliance_v2",
                new_callable=AsyncMock,
                return_value=(False, "96h", "max_attempts reached (2/3)"),
            ),
            patch(
                "app.bot.scheduler.tasks.followup_sender.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_template_service = AsyncMock()
            mock_template_service.send_template = AsyncMock()

            with patch(
                "app.bot.scheduler.tasks.followup_sender.template_service",
                mock_template_service,
            ):
                sender = FollowupSender(session_factory=mock_factory)
                await sender.run()

        from app.models.lead_event import LeadEvent

        discard_events = [
            e for e in recorded_events
            if isinstance(e, LeadEvent) and e.event_type == "auto_discarded_96h"
        ]
        assert len(discard_events) == 1

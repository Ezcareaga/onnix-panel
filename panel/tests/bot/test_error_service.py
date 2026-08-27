"""Tests for BotErrorService.

Plan 71-03: Task 7 — unit tests for error recording, counting, and
auto-disable logic.  All tests use mocked sessions; no real DB required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.services.error_service import BotErrorService, _MAX_ERROR_MESSAGE_LENGTH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_session(*, commit_ok: bool = True):
    """Build a mock AsyncSession.

    If *commit_ok* is False, commit will raise.
    """
    session = AsyncMock()
    session.add = MagicMock()
    if not commit_ok:
        session.commit = AsyncMock(side_effect=Exception("db write failed"))
    else:
        session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.execute = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Tests: record_error inserts row
# ---------------------------------------------------------------------------

class TestRecordError:
    """record_error inserts a BotError into the session."""

    @pytest.mark.asyncio
    async def test_record_error_adds_and_commits(self):
        """A BotError is added to the session and committed."""
        session = _make_mock_session()
        svc = BotErrorService(workflow="telegram")

        await svc.record_error(
            session,
            "something went wrong",
            node="webhook_process",
            chat_id="12345",
        )

        session.add.assert_called_once()
        error_obj = session.add.call_args[0][0]
        assert error_obj.workflow == "telegram"
        assert error_obj.node == "webhook_process"
        assert error_obj.error_message == "something went wrong"
        assert error_obj.chat_id == "12345"
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_record_error_truncates_long_messages(self):
        """Error messages longer than _MAX_ERROR_MESSAGE_LENGTH are truncated."""
        session = _make_mock_session()
        svc = BotErrorService(workflow="whatsapp")

        long_msg = "x" * 5000
        await svc.record_error(session, long_msg)

        error_obj = session.add.call_args[0][0]
        assert len(error_obj.error_message) == _MAX_ERROR_MESSAGE_LENGTH

    @pytest.mark.asyncio
    async def test_record_error_none_message(self):
        """None error_message is stored as None."""
        session = _make_mock_session()
        svc = BotErrorService(workflow="telegram")

        await svc.record_error(session, None)

        error_obj = session.add.call_args[0][0]
        assert error_obj.error_message is None

    @pytest.mark.asyncio
    async def test_record_error_failure_is_non_fatal(self):
        """If the DB write fails, record_error does not raise."""
        session = _make_mock_session(commit_ok=False)
        svc = BotErrorService(workflow="telegram")

        # Should not raise
        await svc.record_error(session, "boom")

        session.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: count_recent
# ---------------------------------------------------------------------------

class TestCountRecent:
    """count_recent returns error count within the time window."""

    @pytest.mark.asyncio
    async def test_count_recent_returns_count(self):
        """Returns the scalar count from the query."""
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 5
        session.execute = AsyncMock(return_value=mock_result)

        svc = BotErrorService(workflow="telegram")
        count = await svc.count_recent(session, window_minutes=15)

        assert count == 5
        session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_count_recent_zero(self):
        """Returns 0 when no recent errors."""
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        session.execute = AsyncMock(return_value=mock_result)

        svc = BotErrorService(workflow="whatsapp")
        count = await svc.count_recent(session, window_minutes=30)

        assert count == 0


# ---------------------------------------------------------------------------
# Tests: check_and_disable
# ---------------------------------------------------------------------------

class TestCheckAndDisable:
    """check_and_disable auto-disables bot when threshold exceeded."""

    @pytest.mark.asyncio
    async def test_triggers_at_threshold(self):
        """Bot is disabled when count >= threshold."""
        session = _make_mock_session()
        # count_recent will return 3
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 3

        # First execute call = count_recent, second = UPDATE bot_settings
        call_count = {"n": 0}

        async def fake_execute(stmt, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return mock_count_result
            return MagicMock()

        session.execute = AsyncMock(side_effect=fake_execute)

        svc = BotErrorService(workflow="telegram")

        with patch("app.bot.services.admin_notifier.get_admin_notifier") as mock_notifier_factory:
            mock_notifier = AsyncMock()
            mock_notifier.notify_bot_disabled = AsyncMock(return_value=True)
            mock_notifier_factory.return_value = mock_notifier

            result = await svc.check_and_disable(session, threshold=3)

        assert result is True
        # Commit should be called (to persist the bot_enabled=false)
        session.commit.assert_awaited()
        mock_notifier.notify_bot_disabled.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_action_below_threshold(self):
        """Bot is NOT disabled when count < threshold."""
        session = _make_mock_session()
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 2

        session.execute = AsyncMock(return_value=mock_count_result)

        svc = BotErrorService(workflow="telegram")
        result = await svc.check_and_disable(session, threshold=3)

        assert result is False
        # Only the count query should have been executed
        assert session.execute.await_count == 1
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_check_and_disable_failure_is_non_fatal(self):
        """If check_and_disable fails, it returns False and does not raise."""
        session = _make_mock_session()
        session.execute = AsyncMock(side_effect=Exception("db down"))

        svc = BotErrorService(workflow="telegram")
        result = await svc.check_and_disable(session, threshold=3)

        assert result is False


# ---------------------------------------------------------------------------
# Un apagado automatico no puede ser invisible
# ---------------------------------------------------------------------------

class TestElApagadoDejaRastro:
    """El disyuntor se queda, pero deja dicho por que salto.

    El unico aviso era `notify_bot_disabled`, que sale por Telegram — caido
    (404), igual que el SMTP (535). Sin rastro, `bot_enabled='false'` en la
    tabla de settings es indistinguible de un apagado a mano desde el panel.

    `bot_settings` es key/value y el formulario renderiza TODAS las filas no
    sensibles, asi que la fila nueva aparece sola en /settings.
    """

    @pytest.mark.asyncio
    async def test_escribe_bot_disabled_reason(self):
        session = _make_mock_session()
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 3

        ejecutadas: list[str] = []

        async def fake_execute(stmt, *args, **kwargs):
            ejecutadas.append(str(stmt))
            if len(ejecutadas) == 1:
                return mock_count_result
            return MagicMock()

        session.execute = AsyncMock(side_effect=fake_execute)
        svc = BotErrorService(workflow="whatsapp")

        with patch("app.bot.services.admin_notifier.get_admin_notifier") as factory:
            factory.return_value = AsyncMock()
            resultado = await svc.check_and_disable(session, threshold=3)

        assert resultado is True

        upserts = [q for q in ejecutadas if "bot_settings" in q and "INSERT" in q]
        assert upserts, (
            "el apagado automatico tiene que dejar el motivo en bot_settings"
        )

        # El motivo viaja como parametro, no en el SQL: se verifica en la llamada.
        motivos = [
            kwargs_o_args
            for call in session.execute.await_args_list
            for kwargs_o_args in call.args[1:]
            if isinstance(kwargs_o_args, dict)
            and kwargs_o_args.get("key") == "bot_disabled_reason"
        ]
        assert len(motivos) == 1, "falta el parametro key=bot_disabled_reason"
        valor = motivos[0]["value"]
        assert "3 errores" in valor
        assert "whatsapp" in valor

    @pytest.mark.asyncio
    async def test_si_el_rastro_falla_el_apagado_igual_vale(self):
        """Dejar el motivo es best-effort: no puede volver reversible el apagado."""
        session = _make_mock_session()
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 5

        llamadas = {"n": 0}

        async def fake_execute(stmt, *args, **kwargs):
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                return mock_count_result
            if llamadas["n"] == 3:  # el upsert del motivo
                raise Exception("bot_settings no escribible")
            return MagicMock()

        session.execute = AsyncMock(side_effect=fake_execute)
        svc = BotErrorService(workflow="whatsapp")

        with patch("app.bot.services.admin_notifier.get_admin_notifier") as factory:
            factory.return_value = AsyncMock()
            resultado = await svc.check_and_disable(session, threshold=3)

        assert resultado is True

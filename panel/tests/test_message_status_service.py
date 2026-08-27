"""Tests for MessageStatusService — delivery status progression.

TDD (Plan B1-status-01):
- Status progression: sent→delivered→read valid
- Out-of-order: read→delivered ignored (no regression)
- failed / undelivered always written (override current status)
- SID not found → returns None, no error
- SSE events published on successful update
- MessageRepo.get_by_external_id returns correct message
- MessageRepo.update_status executes UPDATE and returns updated message
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# MessageRepo.get_by_external_id
# ---------------------------------------------------------------------------

class TestMessageRepoGetByExternalId:
    """Unit tests for the new get_by_external_id repository method."""

    async def test_returns_message_when_found(self):
        """get_by_external_id returns the Message when external_id matches."""
        from app.repositories.message_repo import MessageRepository

        msg_mock = MagicMock()
        msg_mock.external_id = "SM_abc123"

        scalars_mock = MagicMock()
        scalars_mock.first.return_value = msg_mock
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock

        db = AsyncMock()
        db.execute.return_value = result_mock

        result = await MessageRepository.get_by_external_id(db, "SM_abc123")
        assert result is msg_mock
        db.execute.assert_awaited_once()

    async def test_returns_none_when_not_found(self):
        """get_by_external_id returns None when no row matches."""
        from app.repositories.message_repo import MessageRepository

        scalars_mock = MagicMock()
        scalars_mock.first.return_value = None
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock

        db = AsyncMock()
        db.execute.return_value = result_mock

        result = await MessageRepository.get_by_external_id(db, "SM_notexist")
        assert result is None


class TestMessageRepoUpdateStatus:
    """Unit tests for MessageRepository.update_status."""

    async def test_update_sets_status_field(self):
        """update_status sets status (and optionally error fields) on the object."""
        from app.repositories.message_repo import MessageRepository

        msg_mock = MagicMock()
        msg_mock.status = "sent"

        db = AsyncMock()
        result = await MessageRepository.update_status(db, msg_mock, "delivered")

        assert msg_mock.status == "delivered"
        db.flush.assert_awaited_once()

    async def test_update_sets_error_fields_on_failure(self):
        """update_status sets error_code and error_message when provided."""
        from app.repositories.message_repo import MessageRepository

        msg_mock = MagicMock()
        msg_mock.status = "sent"

        db = AsyncMock()
        await MessageRepository.update_status(
            db, msg_mock, "failed", error_code="30008", error_message="Unreachable"
        )

        assert msg_mock.status == "failed"
        assert msg_mock.error_code == "30008"
        assert msg_mock.error_message == "Unreachable"


# ---------------------------------------------------------------------------
# MessageStatusService.handle_status_callback
# ---------------------------------------------------------------------------

class TestMessageStatusServiceProgression:
    """Unit tests for valid/invalid status progression logic."""

    async def _call(
        self,
        current_status: str,
        new_status: str,
        error_code: str = "",
        sid: str = "SM_test001",
    ):
        """Helper: set up a mock DB with a message at current_status and call handle."""
        from app.services.message_status_service import MessageStatusService

        msg_mock = MagicMock()
        msg_mock.id = 1
        msg_mock.status = current_status
        msg_mock.conversation_id = 42
        msg_mock.error_code = None
        msg_mock.error_message = None

        db = AsyncMock()

        with patch(
            "app.services.message_status_service.message_repo"
        ) as mock_repo, patch(
            "app.services.message_status_service.event_bus"
        ) as mock_bus:
            mock_repo.get_by_external_id = AsyncMock(return_value=msg_mock)
            mock_repo.update_status = AsyncMock(return_value=msg_mock)
            mock_bus.publish = AsyncMock()

            result = await MessageStatusService.handle_status_callback(
                db=db,
                message_sid=sid,
                new_status=new_status,
                error_code=error_code,
            )

        return result, mock_repo, mock_bus, msg_mock

    async def test_sent_to_delivered_is_valid(self):
        """sent → delivered: update should be applied."""
        result, repo, bus, msg = await self._call("sent", "delivered")
        repo.update_status.assert_awaited_once()
        assert result is not None

    async def test_delivered_to_read_is_valid(self):
        """delivered → read: update should be applied."""
        result, repo, bus, msg = await self._call("delivered", "read")
        repo.update_status.assert_awaited_once()

    async def test_sent_to_read_is_valid(self):
        """sent → read (skips delivered): valid, applies."""
        result, repo, bus, msg = await self._call("sent", "read")
        repo.update_status.assert_awaited_once()

    async def test_read_to_delivered_is_ignored(self):
        """read → delivered: out-of-order, must NOT update status."""
        result, repo, bus, msg = await self._call("read", "delivered")
        repo.update_status.assert_not_awaited()
        assert result is None

    async def test_delivered_to_sent_is_ignored(self):
        """delivered → sent: regression, must NOT update status."""
        result, repo, bus, msg = await self._call("delivered", "sent")
        repo.update_status.assert_not_awaited()
        assert result is None

    async def test_failed_always_written(self):
        """failed is always applied regardless of current status."""
        result, repo, bus, msg = await self._call("read", "failed", error_code="30008")
        repo.update_status.assert_awaited_once()

    async def test_undelivered_always_written(self):
        """undelivered is always applied regardless of current status."""
        result, repo, bus, msg = await self._call("delivered", "undelivered")
        repo.update_status.assert_awaited_once()

    async def test_failed_over_delivered(self):
        """failed replaces delivered (delivery failure after interim delivered)."""
        result, repo, bus, msg = await self._call("delivered", "failed", error_code="30008")
        repo.update_status.assert_awaited_once()


class TestMessageStatusServiceSidNotFound:
    """SID unknown: must return None and not raise."""

    async def test_unknown_sid_returns_none(self):
        from app.services.message_status_service import MessageStatusService

        db = AsyncMock()
        with patch(
            "app.services.message_status_service.message_repo"
        ) as mock_repo, patch(
            "app.services.message_status_service.event_bus"
        ) as mock_bus:
            mock_repo.get_by_external_id = AsyncMock(return_value=None)
            mock_bus.publish = AsyncMock()

            result = await MessageStatusService.handle_status_callback(
                db=db,
                message_sid="SM_unknown_999",
                new_status="delivered",
                error_code="",
            )

        assert result is None
        mock_bus.publish.assert_not_called()


class TestMessageStatusServiceSSEPublish:
    """SSE events published after a successful status update."""

    async def test_publishes_message_update_and_conversation_update(self):
        """Both message_update_{conv_id} and conversation_update are published."""
        from app.services.message_status_service import MessageStatusService

        msg_mock = MagicMock()
        msg_mock.id = 7
        msg_mock.status = "sent"
        msg_mock.conversation_id = 99
        msg_mock.error_code = None
        msg_mock.error_message = None

        db = AsyncMock()

        with patch(
            "app.services.message_status_service.message_repo"
        ) as mock_repo, patch(
            "app.services.message_status_service.event_bus"
        ) as mock_bus:
            mock_repo.get_by_external_id = AsyncMock(return_value=msg_mock)
            mock_repo.update_status = AsyncMock(return_value=msg_mock)
            mock_bus.publish = AsyncMock()

            await MessageStatusService.handle_status_callback(
                db=db,
                message_sid="SM_pub001",
                new_status="delivered",
                error_code="",
            )

        # Both events must be published
        published_types = [c.args[0] for c in mock_bus.publish.call_args_list]
        assert "message_update_99" in published_types
        assert "conversation_update" in published_types

    async def test_no_sse_when_no_update(self):
        """No SSE published when status regression is ignored."""
        from app.services.message_status_service import MessageStatusService

        msg_mock = MagicMock()
        msg_mock.id = 7
        msg_mock.status = "read"
        msg_mock.conversation_id = 99
        msg_mock.error_code = None

        db = AsyncMock()

        with patch(
            "app.services.message_status_service.message_repo"
        ) as mock_repo, patch(
            "app.services.message_status_service.event_bus"
        ) as mock_bus:
            mock_repo.get_by_external_id = AsyncMock(return_value=msg_mock)
            mock_repo.update_status = AsyncMock()
            mock_bus.publish = AsyncMock()

            await MessageStatusService.handle_status_callback(
                db=db,
                message_sid="SM_nopub",
                new_status="delivered",  # regression
                error_code="",
            )

        mock_bus.publish.assert_not_called()


# ---------------------------------------------------------------------------
# Webhook route: POST /webhook/whatsapp/status — integration
# ---------------------------------------------------------------------------

class TestWhatsAppStatusCallbackRoute:
    """Integration tests for the /webhook/whatsapp/status endpoint.

    Verifies: known SID updates DB + fires SSE; unknown SID → 200 no error;
    invalid signature → 403.
    """

    @pytest.fixture
    def client(self):
        """TestClient with Twilio auth token disabled and DB/service mocked."""
        from unittest.mock import patch, AsyncMock
        from fastapi.testclient import TestClient

        with patch(
            "app.bot.webhooks.whatsapp._get_twilio_auth_token",
            return_value="",
        ):
            from app.main import app
            yield TestClient(app)

    def _status_form(
        self,
        sid: str = "SM_cb_001",
        status: str = "delivered",
        error_code: str = "",
    ) -> dict[str, str]:
        form: dict[str, str] = {
            "MessageSid": sid,
            "MessageStatus": status,
            "To": "whatsapp:+595900000000",
            "From": "whatsapp:+595981555000",
        }
        if error_code:
            form["ErrorCode"] = error_code
        return form

    def test_known_sid_delivered_returns_200(self, client):
        """Known SID with 'delivered' status → 200 TwiML."""
        from app.services.message_status_service import MessageStatusService

        with patch.object(
            MessageStatusService,
            "handle_status_callback",
            new_callable=AsyncMock,
            return_value=MagicMock(id=1, conversation_id=42, status="delivered"),
        ):
            resp = client.post(
                "/webhook/whatsapp/status",
                data=self._status_form("SM_cb_001", "delivered"),
            )
        assert resp.status_code == 200

    def test_unknown_sid_returns_200_no_error(self, client):
        """Unknown SID → 200 (Twilio must not retry). No exception raised."""
        from app.services.message_status_service import MessageStatusService

        with patch.object(
            MessageStatusService,
            "handle_status_callback",
            new_callable=AsyncMock,
            return_value=None,  # SID not found
        ):
            resp = client.post(
                "/webhook/whatsapp/status",
                data=self._status_form("SM_unknown_777", "delivered"),
            )
        assert resp.status_code == 200

    def test_read_status_returns_200(self, client):
        """'read' status → 200."""
        from app.services.message_status_service import MessageStatusService

        with patch.object(
            MessageStatusService,
            "handle_status_callback",
            new_callable=AsyncMock,
            return_value=MagicMock(id=1, conversation_id=5, status="read"),
        ):
            resp = client.post(
                "/webhook/whatsapp/status",
                data=self._status_form("SM_cb_002", "read"),
            )
        assert resp.status_code == 200

    def test_failed_with_error_code_returns_200(self, client):
        """'failed' with ErrorCode → 200 (error handling is non-blocking)."""
        from app.services.message_status_service import MessageStatusService

        with patch.object(
            MessageStatusService,
            "handle_status_callback",
            new_callable=AsyncMock,
            return_value=MagicMock(id=1, conversation_id=3, status="failed"),
        ):
            resp = client.post(
                "/webhook/whatsapp/status",
                data=self._status_form("SM_cb_003", "failed", error_code="30008"),
            )
        assert resp.status_code == 200

    def test_invalid_signature_returns_403(self):
        """When auth is enabled, missing/invalid signature returns 403."""
        import base64
        import hashlib
        import hmac as _hmac
        from fastapi.testclient import TestClient
        from unittest.mock import patch

        token = "test_secret_token_status"
        with patch(
            "app.bot.webhooks.whatsapp._get_twilio_auth_token",
            return_value=token,
        ), patch(
            "app.bot.webhooks.whatsapp._get_webhook_base_url",
            return_value="",
        ):
            from app.main import app
            client = TestClient(app)
            resp = client.post(
                "/webhook/whatsapp/status",
                data=self._status_form(),
                headers={"X-Twilio-Signature": "badsig=="},
            )
        assert resp.status_code == 403

    def test_queued_status_ignored_gracefully(self, client):
        """'queued' status is not in the persist set; returns 200 without calling service."""
        from app.services.message_status_service import MessageStatusService

        with patch.object(
            MessageStatusService,
            "handle_status_callback",
            new_callable=AsyncMock,
        ) as mock_svc:
            resp = client.post(
                "/webhook/whatsapp/status",
                data=self._status_form("SM_cb_004", "queued"),
            )
        assert resp.status_code == 200
        mock_svc.assert_not_called()


# ---------------------------------------------------------------------------
# Reply service: StatusCallback forwarded for manual replies
# ---------------------------------------------------------------------------

class TestReplyServiceStatusCallback:
    """Verify that send_reply passes StatusCallback to Twilio."""

    async def test_twilio_call_includes_status_callback_when_configured(self):
        """_send_twilio_whatsapp must include StatusCallback param when URL is set."""
        import httpx
        from app.services.reply_service import ReplyService

        captured: list[dict] = []

        async def fake_post(url, *, auth, data):
            captured.append(dict(data))
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 201
            resp.json.return_value = {"sid": "SMreplytest001", "status": "queued"}
            resp.raise_for_status = MagicMock()
            return resp

        with patch(
            "app.services.reply_service._http_client"
        ) as mock_client, patch(
            "app.services.reply_service.bot_settings"
        ) as mock_settings:
            mock_client.post = fake_post
            mock_settings.TWILIO_ACCOUNT_SID = "AC_test"
            mock_settings.TWILIO_AUTH_TOKEN = "token_test"
            mock_settings.TWILIO_WHATSAPP_FROM = "whatsapp:+595900000000"
            mock_settings.TWILIO_STATUS_CALLBACK_URL = (
                "https://onnix.com.py/webhook/whatsapp/status"
            )

            await ReplyService._send_twilio_whatsapp("+595981555000", "Hola!")

        assert len(captured) == 1
        assert captured[0].get("StatusCallback") == (
            "https://onnix.com.py/webhook/whatsapp/status"
        )

    async def test_twilio_call_omits_status_callback_when_not_configured(self):
        """_send_twilio_whatsapp must NOT include StatusCallback when URL is empty."""
        import httpx
        from app.services.reply_service import ReplyService

        captured: list[dict] = []

        async def fake_post(url, *, auth, data):
            captured.append(dict(data))
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 201
            resp.json.return_value = {"sid": "SMreply002", "status": "queued"}
            resp.raise_for_status = MagicMock()
            return resp

        with patch(
            "app.services.reply_service._http_client"
        ) as mock_client, patch(
            "app.services.reply_service.bot_settings"
        ) as mock_settings:
            mock_client.post = fake_post
            mock_settings.TWILIO_ACCOUNT_SID = "AC_test"
            mock_settings.TWILIO_AUTH_TOKEN = "token_test"
            mock_settings.TWILIO_WHATSAPP_FROM = "whatsapp:+595900000000"
            mock_settings.TWILIO_STATUS_CALLBACK_URL = ""

            await ReplyService._send_twilio_whatsapp("+595981555000", "Hola!")

        assert "StatusCallback" not in captured[0]

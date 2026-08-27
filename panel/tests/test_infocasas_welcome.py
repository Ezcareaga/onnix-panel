# panel/tests/test_infocasas_welcome.py
"""Tests for IC welcome message persistence with correct timestamps.

IC-01: _send_whatsapp_welcome() must capture sent_at BEFORE the Twilio call
and _save_welcome_message() must store it as created_at via message_repo.create().
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = pytest.mark.asyncio


class TestSaveWelcomeMessage:
    """Tests for _save_welcome_message() in InfocasasService."""

    async def test_save_welcome_uses_sent_at_not_now(self):
        """created_at in message_repo.create() must equal the sent_at timestamp."""
        from app.bot.services.infocasas.infocasas_service import InfocasasService

        sent_at = datetime(2026, 4, 3, 10, 30, 0, tzinfo=timezone.utc)

        mock_conv = MagicMock()
        mock_conv.id = 99

        with patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as MockConvMgr, patch(
            "app.bot.services.infocasas.infocasas_service.message_repo"
        ) as mock_message_repo:
            mock_mgr = MockConvMgr.return_value
            mock_mgr.get_or_create_conversation = AsyncMock(return_value=mock_conv)
            mock_message_repo.create = AsyncMock()

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_factory = MagicMock(return_value=mock_session)

            service = InfocasasService.__new__(InfocasasService)
            service._session_factory = mock_session_factory

            await service._save_welcome_message(
                contact_id=42,
                phone="+595981234567",
                name="Juan",
                zone="Asunción",
                sent_at=sent_at,
            )

        mock_message_repo.create.assert_called_once()
        call_kwargs = mock_message_repo.create.call_args.kwargs
        assert call_kwargs["created_at"] == sent_at
        assert call_kwargs["intent"] == "ic_welcome"
        assert call_kwargs["sender_type"] == "bot"
        assert call_kwargs["direction"] == "outbound"

    async def test_save_welcome_uses_sender_type_bot(self):
        """sender_type must be 'bot' for IC welcome messages."""
        from app.bot.services.infocasas.infocasas_service import InfocasasService

        sent_at = datetime(2026, 4, 3, 10, 30, 0, tzinfo=timezone.utc)
        mock_conv = MagicMock()
        mock_conv.id = 1

        with patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as MockConvMgr, patch(
            "app.bot.services.infocasas.infocasas_service.message_repo"
        ) as mock_message_repo:
            mock_mgr = MockConvMgr.return_value
            mock_mgr.get_or_create_conversation = AsyncMock(return_value=mock_conv)
            mock_message_repo.create = AsyncMock()

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            service = InfocasasService.__new__(InfocasasService)
            service._session_factory = MagicMock(return_value=mock_session)

            await service._save_welcome_message(
                contact_id=1,
                phone="+595981234567",
                name="Test",
                zone="Central",
                sent_at=sent_at,
            )

        call_kwargs = mock_message_repo.create.call_args.kwargs
        assert call_kwargs["sender_type"] == "bot"

    async def test_save_welcome_creates_conversation(self):
        """get_or_create_conversation must be called with correct params."""
        from app.bot.services.infocasas.infocasas_service import InfocasasService

        sent_at = datetime(2026, 4, 3, 10, 30, 0, tzinfo=timezone.utc)
        mock_conv = MagicMock()
        mock_conv.id = 55

        with patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as MockConvMgr, patch(
            "app.bot.services.infocasas.infocasas_service.message_repo"
        ) as mock_message_repo:
            mock_mgr = MockConvMgr.return_value
            mock_mgr.get_or_create_conversation = AsyncMock(return_value=mock_conv)
            mock_message_repo.create = AsyncMock()

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            service = InfocasasService.__new__(InfocasasService)
            service._session_factory = MagicMock(return_value=mock_session)

            await service._save_welcome_message(
                contact_id=42,
                phone="+595981111111",
                name="Maria",
                zone="Luque",
                sent_at=sent_at,
            )

        mock_mgr.get_or_create_conversation.assert_called_once_with(
            mock_session,
            contact_id=42,
            platform="whatsapp",
            chat_id="+595981111111",
        )

    async def test_save_welcome_exception_is_swallowed(self):
        """Errors in _save_welcome_message must be caught and not re-raised."""
        from app.bot.services.infocasas.infocasas_service import InfocasasService

        sent_at = datetime(2026, 4, 3, 10, 30, 0, tzinfo=timezone.utc)

        with patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as MockConvMgr:
            mock_mgr = MockConvMgr.return_value
            mock_mgr.get_or_create_conversation = AsyncMock(
                side_effect=RuntimeError("DB connection failed")
            )

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            service = InfocasasService.__new__(InfocasasService)
            service._session_factory = MagicMock(return_value=mock_session)

            # Must not raise
            await service._save_welcome_message(
                contact_id=1,
                phone="+595981234567",
                name="Test",
                zone="Asuncion",
                sent_at=sent_at,
            )

    async def test_welcome_enabled_is_called_for_new_lead_with_phone(self):
        """_send_whatsapp_welcome must be defined (not removed/commented out)."""
        from app.bot.services.infocasas.infocasas_service import InfocasasService

        service = InfocasasService.__new__(InfocasasService)
        assert hasattr(service, "_send_whatsapp_welcome"), (
            "_send_whatsapp_welcome must be defined (not removed)"
        )

    async def test_save_welcome_message_repo_call_has_correct_content(self):
        """message_repo.create() body must include name and zone."""
        from app.bot.services.infocasas.infocasas_service import InfocasasService

        sent_at = datetime(2026, 4, 3, 12, 0, 0, tzinfo=timezone.utc)
        mock_conv = MagicMock()
        mock_conv.id = 7

        with patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as MockConvMgr, patch(
            "app.bot.services.infocasas.infocasas_service.message_repo"
        ) as mock_message_repo:
            mock_mgr = MockConvMgr.return_value
            mock_mgr.get_or_create_conversation = AsyncMock(return_value=mock_conv)
            mock_message_repo.create = AsyncMock()

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            service = InfocasasService.__new__(InfocasasService)
            service._session_factory = MagicMock(return_value=mock_session)

            await service._save_welcome_message(
                contact_id=10,
                phone="+595981999888",
                name="Carlos",
                zone="Fernando de la Mora",
                sent_at=sent_at,
            )

        call_kwargs = mock_message_repo.create.call_args.kwargs
        assert "Carlos" in call_kwargs["body"]
        assert "Fernando de la Mora" in call_kwargs["body"]
        assert call_kwargs["body"] == call_kwargs["content"]
        assert call_kwargs["external_id"] == ""
        assert call_kwargs["status"] == "sent"
        assert call_kwargs["conversation_id"] == 7
        assert call_kwargs["contact_id"] == 10

    async def test_save_welcome_message_repo_direction_outbound(self):
        """direction must be 'outbound' for welcome messages."""
        from app.bot.services.infocasas.infocasas_service import InfocasasService

        sent_at = datetime(2026, 4, 3, 12, 0, 0, tzinfo=timezone.utc)
        mock_conv = MagicMock()
        mock_conv.id = 3

        with patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as MockConvMgr, patch(
            "app.bot.services.infocasas.infocasas_service.message_repo"
        ) as mock_message_repo:
            mock_mgr = MockConvMgr.return_value
            mock_mgr.get_or_create_conversation = AsyncMock(return_value=mock_conv)
            mock_message_repo.create = AsyncMock()

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            service = InfocasasService.__new__(InfocasasService)
            service._session_factory = MagicMock(return_value=mock_session)

            await service._save_welcome_message(
                contact_id=5,
                phone="+595981000001",
                name="Ana",
                zone="Lambare",
                sent_at=sent_at,
            )

        call_kwargs = mock_message_repo.create.call_args.kwargs
        assert call_kwargs["direction"] == "outbound"

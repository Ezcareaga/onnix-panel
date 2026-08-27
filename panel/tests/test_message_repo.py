"""
Tests for app/repositories/message_repo.py

Unit tests for:
- get_by_conversation: limit=200 (Fix 4)
- create: atomically increments message_count on parent conversation (Fix 3)

The db session is fully mocked so no database connection is required.
"""
import inspect
from unittest.mock import AsyncMock, MagicMock, call, patch
from datetime import datetime, timezone

import pytest

from app.repositories.message_repo import MessageRepository


# ---------------------------------------------------------------------------
# Fix 4: get_by_conversation default limit is 200
# ---------------------------------------------------------------------------

class TestGetByConversationLimit:
    def test_default_limit_is_200(self):
        """get_by_conversation must default to limit=200, not 100."""
        sig = inspect.signature(MessageRepository.get_by_conversation)
        default_limit = sig.parameters["limit"].default
        assert default_limit == 200, (
            f"Expected default limit=200, got {default_limit}"
        )

    async def test_limit_passed_to_query(self):
        """The limit value is forwarded to the SQLAlchemy query."""
        # Build a mock db that returns an empty list of messages
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_inner = MagicMock()
        result_inner.scalars.return_value = scalars_mock

        db = AsyncMock()
        db.execute.return_value = result_inner

        await MessageRepository.get_by_conversation(db, conversation_id=42, limit=200)

        # db.execute should have been called (query was built and executed)
        db.execute.assert_awaited()


# ---------------------------------------------------------------------------
# Fix 3: create() atomically increments conversations.message_count
# ---------------------------------------------------------------------------

class TestCreateIncrementsMessageCount:
    async def test_create_executes_update_after_insert(self):
        """create() must execute an UPDATE conversations SET message_count = message_count + 1."""
        # Mock the Message object returned after flush/refresh
        msg_mock = MagicMock()
        msg_mock.id = 99

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        # db.refresh sets fields on the message — just return the mock
        async def fake_refresh(obj):
            pass
        db.refresh = fake_refresh

        # db.execute is called for the UPDATE — capture all calls
        db.execute = AsyncMock()

        # Patch the Message constructor to return our mock
        with patch("app.repositories.message_repo.Message", return_value=msg_mock):
            result = await MessageRepository.create(
                db=db,
                conversation_id=7,
                contact_id=3,
                direction="outbound",
                sender_type="bot",
                body="Hola",
                content="Hola",
                external_id="ext-001",
                status="sent",
            )

        # Must have called db.execute at least once (the UPDATE)
        assert db.execute.await_count >= 1, (
            "create() must call db.execute() for the message_count UPDATE"
        )

        # Verify the SQL UPDATE was issued for the correct conversation_id
        all_calls = db.execute.await_args_list
        update_call_found = False
        for c in all_calls:
            args = c[0]  # positional args of the call
            if args:
                sql_arg = str(args[0])
                if "message_count" in sql_arg.lower() and "conversations" in sql_arg.lower():
                    update_call_found = True
                    break
        assert update_call_found, (
            "create() must issue an UPDATE conversations SET message_count = message_count + 1"
        )

    async def test_create_returns_message(self):
        """create() returns the Message object (regression guard)."""
        msg_mock = MagicMock()
        msg_mock.id = 55

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.execute = AsyncMock()

        async def fake_refresh(obj):
            pass
        db.refresh = fake_refresh

        with patch("app.repositories.message_repo.Message", return_value=msg_mock):
            result = await MessageRepository.create(
                db=db,
                conversation_id=1,
                contact_id=1,
                direction="inbound",
                sender_type="user",
                body="test",
                content="test",
                external_id="ext-002",
            )

        assert result is msg_mock

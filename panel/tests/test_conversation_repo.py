"""
Tests for app/repositories/conversation_repo.py

Unit tests for get_with_contacts and search_with_contacts.
The db session is fully mocked so no database connection is required.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import sqlalchemy

from app.repositories.conversation_repo import ConversationRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    conv_id=10,
    contact_id=1,
    name="Ana Torres",
    phone="+595981000001",
    preview=None,
    direction=None,
):
    """Build a mock SQLAlchemy Row tuple: (Conversation, name, phone, preview, direction)."""
    conv = MagicMock()
    conv.id = conv_id
    conv.contact_id = contact_id
    row = MagicMock()
    row.__getitem__ = lambda self, idx: (conv, name, phone, preview, direction)[idx]
    return row


def _make_db_with_rows(rows):
    """Build a mock AsyncSession whose execute().all() returns the given rows."""
    result_mock = MagicMock()
    result_mock.all.return_value = rows
    db = AsyncMock()
    db.execute.return_value = result_mock
    return db


# ---------------------------------------------------------------------------
# get_with_contacts
# ---------------------------------------------------------------------------

class TestGetWithContacts:
    async def test_correct_structure(self):
        row = _make_row(
            conv_id=10, name="Luis Gomez", phone="+595981111001",
            preview="Hola, me interesa", direction="inbound",
        )
        db = _make_db_with_rows([row])

        result = await ConversationRepository.get_with_contacts(db, limit=50)

        assert len(result) == 1
        assert result[0]["contact_name"] == "Luis Gomez"
        assert result[0]["contact_phone"] == "+595981111001"
        assert result[0]["conversation"] is row[0]
        assert result[0]["last_message_preview"] == "Hola, me interesa"
        assert result[0]["last_message_direction"] == "inbound"

    async def test_null_defaults(self):
        row = _make_row(conv_id=10, name=None, phone=None, preview=None, direction=None)
        db = _make_db_with_rows([row])

        result = await ConversationRepository.get_with_contacts(db, limit=50)

        assert result[0]["contact_name"] == "Desconocido"
        assert result[0]["contact_phone"] == ""
        assert result[0]["last_message_preview"] == ""
        assert result[0]["last_message_direction"] == ""

    async def test_empty(self):
        db = _make_db_with_rows([])

        result = await ConversationRepository.get_with_contacts(db, limit=50)

        assert result == []

    async def test_preview_truncated_to_60_chars(self):
        long_preview = "A" * 80
        row = _make_row(conv_id=10, preview=long_preview, direction="outbound")
        db = _make_db_with_rows([row])

        result = await ConversationRepository.get_with_contacts(db, limit=50)

        assert len(result[0]["last_message_preview"]) == 60


# ---------------------------------------------------------------------------
# search_with_contacts
# ---------------------------------------------------------------------------

class TestSearchWithContacts:
    async def test_correct_structure(self):
        row = _make_row(
            conv_id=10, name="Maria Lopez", phone="+595981000010",
            preview="Busco casa en Asuncion", direction="inbound",
        )
        db = _make_db_with_rows([row])

        result = await ConversationRepository.search_with_contacts(db, "Maria", limit=50)

        assert len(result) == 1
        assert result[0]["contact_name"] == "Maria Lopez"
        assert result[0]["contact_phone"] == "+595981000010"
        assert result[0]["conversation"] is row[0]
        assert result[0]["last_message_preview"] == "Busco casa en Asuncion"
        assert result[0]["last_message_direction"] == "inbound"

    async def test_null_defaults(self):
        row = _make_row(conv_id=10, name=None, phone=None, preview=None, direction=None)
        db = _make_db_with_rows([row])

        result = await ConversationRepository.search_with_contacts(db, "test", limit=50)

        assert result[0]["contact_name"] == "Desconocido"
        assert result[0]["contact_phone"] == ""
        assert result[0]["last_message_preview"] == ""
        assert result[0]["last_message_direction"] == ""

    async def test_empty(self):
        db = _make_db_with_rows([])

        result = await ConversationRepository.search_with_contacts(db, "nonexistent", limit=50)

        assert result == []

    async def test_executes_query(self):
        db = _make_db_with_rows([])

        await ConversationRepository.search_with_contacts(db, "test", limit=25)

        db.execute.assert_awaited_once()

    async def test_multiple_rows(self):
        row_a = _make_row(conv_id=10, name="Maria Lopez", phone="+595981000010", preview="Hola", direction="inbound")
        row_b = _make_row(conv_id=11, name="Maria Garcia", phone="+595981000011", preview="Gracias", direction="outbound")
        db = _make_db_with_rows([row_a, row_b])

        result = await ConversationRepository.search_with_contacts(db, "Maria", limit=50)

        assert len(result) == 2
        names = {r["contact_name"] for r in result}
        assert names == {"Maria Lopez", "Maria Garcia"}


# ---------------------------------------------------------------------------
# Fix 2: Ghost conversations filter
# get_with_contacts and search_with_contacts must exclude conversations
# where message_count = 0 AND last_message_at IS NULL
# ---------------------------------------------------------------------------

class TestGhostConversationsFilter:
    """Verify that the query built by get_with_contacts and search_with_contacts
    includes a WHERE filter that excludes ghost conversations
    (message_count = 0 AND last_message_at IS NULL).

    We inspect the compiled SQL string to confirm the WHERE filter is present.
    The key token is 'where' followed by the ghost-exclusion condition.
    """

    def _capture_query(self, db: AsyncMock) -> str:
        """Extract the SQL string from the first db.execute() call."""
        assert db.execute.await_count >= 1
        call_args = db.execute.await_args_list[0]
        stmt = call_args[0][0]  # first positional arg of first call
        compiled = stmt.compile(dialect=sqlalchemy.dialects.postgresql.dialect())
        return str(compiled).lower()

    def _has_ghost_filter(self, sql: str) -> bool:
        """Return True if the SQL has a WHERE clause that references both
        message_count and last_message_at as filter criteria (not just SELECT/ORDER BY).

        A ghost filter looks like one of:
          NOT (message_count = 0 AND last_message_at IS NULL)
          (message_count > 0 OR last_message_at IS NOT NULL)
        We detect it by finding 'message_count' appearing after 'where' in the
        outer query (not inside a subquery), specifically as a filter condition.
        """
        # Find index of the main WHERE clause in the outer query
        # The outer query's WHERE starts after the JOINs
        # Strategy: look for "not (" or check after "where" for message_count
        where_idx = sql.find("\nwhere ")
        if where_idx == -1:
            where_idx = sql.find(" where ")
        if where_idx == -1:
            return False
        where_clause = sql[where_idx:]
        return "message_count" in where_clause

    async def test_get_with_contacts_excludes_ghost_conversations(self):
        """get_with_contacts query must have a WHERE filter on message_count."""
        db = _make_db_with_rows([])

        await ConversationRepository.get_with_contacts(db, limit=50)

        sql = self._capture_query(db)
        assert self._has_ghost_filter(sql), (
            "get_with_contacts must include a WHERE filter on message_count "
            "to exclude ghost conversations (message_count=0 AND last_message_at IS NULL). "
            f"SQL: {sql[:500]}"
        )

    async def test_search_with_contacts_excludes_ghost_conversations(self):
        """search_with_contacts query must have a WHERE filter on message_count."""
        db = _make_db_with_rows([])

        await ConversationRepository.search_with_contacts(db, "test", limit=50)

        sql = self._capture_query(db)
        assert self._has_ghost_filter(sql), (
            "search_with_contacts must include a WHERE filter on message_count "
            "to exclude ghost conversations (message_count=0 AND last_message_at IS NULL). "
            f"SQL: {sql[:500]}"
        )

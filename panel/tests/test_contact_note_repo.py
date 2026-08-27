"""Tests for app/repositories/contact_note_repo.py (Phase 93)."""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.repositories.contact_note_repo import ContactNoteRepository


pytestmark = pytest.mark.asyncio


class TestContactNoteRepository:

    async def test_create_adds_and_flushes(self):
        db = AsyncMock()
        note = await ContactNoteRepository.create(db, contact_id=1, user_id=2, content="hola")
        db.add.assert_called_once()
        db.flush.assert_called_once()
        assert note.contact_id == 1
        assert note.user_id == 2
        assert note.content == "hola"

    async def test_get_by_contact_returns_list(self):
        db = AsyncMock()
        mock_note1 = MagicMock()
        mock_note2 = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_note1, mock_note2]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        db.execute.return_value = mock_result
        result = await ContactNoteRepository.get_by_contact(db, contact_id=1)
        assert len(result) == 2

    async def test_get_by_id_found(self):
        db = AsyncMock()
        mock_note = MagicMock()
        db.get.return_value = mock_note
        result = await ContactNoteRepository.get_by_id(db, note_id=1)
        assert result is mock_note

    async def test_update_sets_content_and_updated_at(self):
        db = AsyncMock()
        mock_note = MagicMock()
        db.get.return_value = mock_note
        result = await ContactNoteRepository.update(db, note_id=1, content="nuevo")
        assert mock_note.content == "nuevo"
        assert mock_note.updated_at is not None
        db.flush.assert_called_once()

    async def test_delete_returns_true(self):
        db = AsyncMock()
        mock_note = MagicMock()
        db.get.return_value = mock_note
        result = await ContactNoteRepository.delete(db, note_id=1)
        db.delete.assert_called_once_with(mock_note)
        db.flush.assert_called_once()
        assert result is True

    async def test_delete_not_found_returns_false(self):
        db = AsyncMock()
        db.get.return_value = None
        result = await ContactNoteRepository.delete(db, note_id=99)
        assert result is False

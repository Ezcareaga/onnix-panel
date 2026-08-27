"""Tests for ContactService note management methods (Phase 93)."""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from app.services.contact_service import ContactService


pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_note_repos():
    with (
        patch("app.services.contact_service.contact_repo") as cr,
        patch("app.services.contact_service.lead_event_repo") as ler,
        patch("app.services.contact_service.conversation_repo") as cvr,
        patch("app.services.contact_service.property_repo") as pr,
        patch("app.services.contact_service.contact_note_repo") as cnr,
    ):
        for repo in (cr, ler, cvr, pr, cnr):
            for name in [
                "get_all", "count_all", "get_by_id", "get_by_contact",
                "create", "update", "delete", "get_by_ids", "get_ic_by_refs",
                "get_ic_by_ref", "update_status", "get_detail_views",
            ]:
                if not hasattr(repo, name) or callable(getattr(repo, name)):
                    setattr(repo, name, AsyncMock())
        ler.get_detail_views.return_value = []
        yield {"contact": cr, "lead_event": ler, "note": cnr}


@pytest.fixture
def db():
    session = AsyncMock()
    return session


class TestCreateNote:

    async def test_create_note_ok(self, mock_note_repos, db):
        mock_contact = MagicMock()
        mock_note_repos["contact"].get_by_id.return_value = mock_contact
        mock_note = MagicMock()
        mock_note.id = 42
        mock_note_repos["note"].create.return_value = mock_note

        result, error = await ContactService.create_note(db, contact_id=1, content="Test note", user_id=5)

        assert error is None
        assert result is mock_note
        mock_note_repos["note"].create.assert_called_once_with(db, 1, 5, "Test note")
        mock_note_repos["lead_event"].create.assert_called_once()
        db.commit.assert_called_once()

    async def test_create_note_contact_not_found(self, mock_note_repos, db):
        mock_note_repos["contact"].get_by_id.return_value = None

        result, error = await ContactService.create_note(db, contact_id=999, content="note", user_id=1)

        assert result is None
        assert error == "Contacto no encontrado"
        mock_note_repos["note"].create.assert_not_called()

    async def test_create_note_empty_content(self, mock_note_repos, db):
        mock_note_repos["contact"].get_by_id.return_value = MagicMock()

        result, error = await ContactService.create_note(db, contact_id=1, content="   ", user_id=1)

        assert result is None
        assert error == "El contenido no puede estar vacío"
        mock_note_repos["note"].create.assert_not_called()


class TestUpdateNote:

    async def test_update_note_ok(self, mock_note_repos, db):
        mock_note = MagicMock()
        mock_note.id = 1
        mock_note.contact_id = 10
        mock_note_repos["note"].get_by_id.return_value = mock_note
        updated = MagicMock()
        mock_note_repos["note"].update.return_value = updated

        result, error = await ContactService.update_note(db, note_id=1, content="updated", user_id=5)

        assert error is None
        assert result is updated
        mock_note_repos["note"].update.assert_called_once_with(db, 1, "updated")
        mock_note_repos["lead_event"].create.assert_called_once()
        db.commit.assert_called_once()

    async def test_update_note_not_found(self, mock_note_repos, db):
        mock_note_repos["note"].get_by_id.return_value = None

        result, error = await ContactService.update_note(db, note_id=99, content="x", user_id=1)

        assert result is None
        assert error == "Nota no encontrada"

    async def test_update_note_empty_content(self, mock_note_repos, db):
        result, error = await ContactService.update_note(db, note_id=1, content="", user_id=1)

        assert result is None
        assert error == "El contenido no puede estar vacío"
        mock_note_repos["note"].get_by_id.assert_not_called()


class TestDeleteNote:

    async def test_delete_note_ok(self, mock_note_repos, db):
        mock_note = MagicMock()
        mock_note.id = 1
        mock_note.contact_id = 10
        mock_note_repos["note"].get_by_id.return_value = mock_note
        mock_note_repos["note"].delete.return_value = True

        result, error = await ContactService.delete_note(db, note_id=1, user_id=5)

        assert result is True
        assert error is None
        mock_note_repos["note"].delete.assert_called_once_with(db, 1)
        mock_note_repos["lead_event"].create.assert_called_once()
        db.commit.assert_called_once()

    async def test_delete_note_not_found(self, mock_note_repos, db):
        mock_note_repos["note"].get_by_id.return_value = None

        result, error = await ContactService.delete_note(db, note_id=99, user_id=1)

        assert result is False
        assert error == "Nota no encontrada"


class TestGetNotes:

    async def test_get_notes_delegates_to_repo(self, mock_note_repos, db):
        mock_notes = [MagicMock(), MagicMock()]
        mock_note_repos["note"].get_by_contact.return_value = mock_notes

        result = await ContactService.get_notes(db, contact_id=1)

        assert result == mock_notes
        mock_note_repos["note"].get_by_contact.assert_called_once_with(db, 1)

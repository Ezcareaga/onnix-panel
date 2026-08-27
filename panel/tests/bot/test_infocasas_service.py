"""Tests for InfocasasService — InfoCasas lead capture orchestrator.

Covers:
  - run_poll: auth_failed, fetch_failed, no notifications, new leads,
    duplicates, mark_seen called for all, metrics
  - _process_lead: full flow including upsert, match, log, notify
  - _upsert_contact: new contact, existing by phone, existing by source_id
  - _match_property: IC ref lookup, unknown ref, no code — never writes property_id
  - _log_lead_event: metadata fields
  - _notify_new_lead: correct format, no notifier, exception swallowed
  - _send_whatsapp_welcome: sends template, no phone skip, no template skip,
    delay between min/max
  - mark_seen for all notifications including failures
  - get_infocasas_service: factory returns correct type

All dependencies are mocked; no real DB, network, or Twilio calls.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.services.infocasas.infocasas_service import (
    InfocasasService,
    get_infocasas_service,
)
from app.bot.services.infocasas.lead_parser import ParsedLead


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _make_parsed_lead(
    *,
    consulta_id: str = "66065340",
    name: str = "Nicole Caceres",
    phone: str | None = "+595900000001",
    email: str | None = "nicole@example.com",
    message: str | None = "Me interesa la propiedad",
    property_code: str | None = "OF23CE",
    property_title: str | None = "Casa en Fernando de la Mora",
    listing_city: str | None = "Fernando de la Mora",
    has_whatsapp: bool = True,
    is_reassigned: bool = False,
) -> ParsedLead:
    """Build a ParsedLead with sensible defaults."""
    return ParsedLead(
        consulta_id=consulta_id,
        name=name,
        phone=phone,
        email=email,
        message=message,
        consulta_date=datetime(2026, 3, 28, 14, 30, tzinfo=timezone.utc),
        property_code=property_code,
        property_title=property_title,
        listing_city=listing_city,
        has_whatsapp=has_whatsapp,
        is_reassigned=is_reassigned,
        # New characteristics fields default to None
        listing_type=None,
        listing_operation=None,
        listing_bedrooms=None,
        listing_area_m2=None,
        listing_price=None,
        listing_currency=None,
    )


def _make_notification(
    *,
    notif_id: int = 1001,
    url: str = "/sitio/index.php?mid=consultas&id=66065340",
    seen: bool = False,
    created_at: str = "2026-03-28 14:30:00",
) -> dict:
    return {
        "id": notif_id,
        "url": url,
        "seen": seen,
        "created_at": created_at,
        "text": "Nueva consulta",
        "image": None,
    }


def _make_session_factory(*, session: AsyncMock | None = None) -> MagicMock:
    """Return an async context manager factory wrapping *session*.

    Ensures ``session.add`` is always a plain ``MagicMock`` (not async)
    because ``Session.add()`` is a synchronous method in SQLAlchemy.
    """
    mock_session = session or AsyncMock()
    # session.add is synchronous in SQLAlchemy; override to avoid RuntimeWarning
    mock_session.add = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=mock_ctx)
    return factory


def _make_service(
    *,
    token: str | None = "valid_token",
    notifications: list[dict] | None = None,
    existing_ids: set[str] | None = None,
    lead_data: dict | None = None,
    mark_seen_ok: bool = True,
    notifier: AsyncMock | None = None,
    session: AsyncMock | None = None,
) -> tuple[InfocasasService, MagicMock, MagicMock]:
    """Build an InfocasasService with mocked session_manager and fetcher.

    Returns (service, mock_session_manager, mock_fetcher).
    """
    mock_sm = AsyncMock()
    mock_sm.get_valid_token = AsyncMock(return_value=token)

    mock_fetcher = AsyncMock()
    mock_fetcher.fetch_notifications = AsyncMock(return_value=notifications)
    mock_fetcher.fetch_lead_details = AsyncMock(return_value=lead_data)
    mock_fetcher.mark_seen = AsyncMock(return_value=mark_seen_ok)
    mock_fetcher.check_existing_ids = AsyncMock(return_value=existing_ids or set())

    mock_notifier = notifier or AsyncMock()
    mock_notifier.notify = AsyncMock(return_value=True)

    factory = _make_session_factory(session=session)

    svc = InfocasasService(
        session_manager=mock_sm,
        notification_fetcher=mock_fetcher,
        notifier=mock_notifier,
        session_factory=factory,
    )
    return svc, mock_sm, mock_fetcher


# ---------------------------------------------------------------------------
# TestRunPollAuthFailed
# ---------------------------------------------------------------------------


class TestRunPollAuthFailed:
    """get_valid_token returns None -> auth_failed status."""

    @pytest.mark.asyncio
    async def test_returns_auth_failed_status(self):
        svc, _, _ = _make_service(token=None)
        result = await svc.run_poll()
        assert result["status"] == "auth_failed"

    @pytest.mark.asyncio
    async def test_metrics_all_zero(self):
        svc, _, _ = _make_service(token=None)
        result = await svc.run_poll()
        assert result["processed"] == 0
        assert result["new"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_fetch_notifications_not_called(self):
        svc, _, mock_fetcher = _make_service(token=None)
        await svc.run_poll()
        mock_fetcher.fetch_notifications.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestRunPollFetchFailed
# ---------------------------------------------------------------------------


class TestRunPollFetchFailed:
    """fetch_notifications returns None -> fetch_failed status."""

    @pytest.mark.asyncio
    async def test_returns_fetch_failed_status(self):
        svc, _, _ = _make_service(notifications=None)
        result = await svc.run_poll()
        assert result["status"] == "fetch_failed"

    @pytest.mark.asyncio
    async def test_metrics_all_zero(self):
        svc, _, _ = _make_service(notifications=None)
        result = await svc.run_poll()
        assert result["processed"] == 0
        assert result["new"] == 0

    @pytest.mark.asyncio
    async def test_mark_seen_not_called(self):
        svc, _, mock_fetcher = _make_service(notifications=None)
        await svc.run_poll()
        mock_fetcher.mark_seen.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestRunPollNoNotifications
# ---------------------------------------------------------------------------


class TestRunPollNoNotifications:
    """fetch_notifications returns [] -> ok status, all zeros."""

    @pytest.mark.asyncio
    async def test_returns_ok_status(self):
        svc, _, _ = _make_service(notifications=[])
        result = await svc.run_poll()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_processed_zero(self):
        svc, _, _ = _make_service(notifications=[])
        result = await svc.run_poll()
        assert result["processed"] == 0

    @pytest.mark.asyncio
    async def test_mark_seen_not_called(self):
        svc, _, mock_fetcher = _make_service(notifications=[])
        await svc.run_poll()
        mock_fetcher.mark_seen.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestRunPollWithNewLeads
# ---------------------------------------------------------------------------


class TestRunPollWithNewLeads:
    """Full flow: notifications -> filter -> dedup -> process -> mark_seen."""

    @pytest.fixture
    def lead_data_fixture(self) -> dict:
        return {
            "id": "66065340",
            "message": "Me interesa",
            "created_at": "2026-03-28 14:30:00",
            "from": {
                "name": "Nicole Caceres",
                "email": "nicole@example.com",
                "phone": "+595900000001",
                "whatsapp_phone": None,
                "has_whatsapp": False,
            },
            "listing": {
                "id": "193572330",
                "title": "Casa en Fernando de la Mora",
                "code": "OF23CE",
                "neighborhood": {"name": "Fernando de la Mora"},
            },
        }

    @pytest.mark.asyncio
    async def test_ok_status_with_new_lead(self, lead_data_fixture):
        notif = _make_notification()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_scalar_none_result())
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()

        svc, _, _ = _make_service(
            notifications=[notif],
            lead_data=lead_data_fixture,
            session=mock_session,
        )
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.LeadEventRepository.create",
            new=AsyncMock(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(return_value=None),
        ):
            result = await svc.run_poll()

        assert result["status"] == "ok"
        assert result["new"] == 1

    @pytest.mark.asyncio
    async def test_mark_seen_called_for_all_notifications(self, lead_data_fixture):
        notifs = [_make_notification(notif_id=1001), _make_notification(notif_id=1002)]
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_scalar_none_result())
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()

        svc, _, mock_fetcher = _make_service(
            notifications=notifs,
            lead_data=lead_data_fixture,
            session=mock_session,
        )
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.LeadEventRepository.create",
            new=AsyncMock(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(return_value=None),
        ):
            await svc.run_poll()

        # mark_seen called once per notification
        assert mock_fetcher.mark_seen.await_count == 2
        seen_ids = {call.args[1] for call in mock_fetcher.mark_seen.await_args_list}
        assert seen_ids == {"1001", "1002"}

    @pytest.mark.asyncio
    async def test_processed_matches_notification_count(self, lead_data_fixture):
        notifs = [_make_notification(notif_id=i, url=f"/sitio/index.php?mid=consultas&id={i}") for i in range(1, 4)]
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_scalar_none_result())
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()

        svc, _, _ = _make_service(
            notifications=notifs,
            lead_data=lead_data_fixture,
            session=mock_session,
        )
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.LeadEventRepository.create",
            new=AsyncMock(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(return_value=None),
        ):
            result = await svc.run_poll()

        assert result["processed"] == 3


# ---------------------------------------------------------------------------
# TestRunPollWithDuplicates
# ---------------------------------------------------------------------------


class TestRunPollWithDuplicates:
    """Dedup filters out already-processed leads."""

    @pytest.mark.asyncio
    async def test_skipped_count_reflects_duplicates(self):
        notifs = [
            _make_notification(notif_id=1001, url="/sitio/index.php?mid=consultas&id=66065340"),
            _make_notification(notif_id=1002, url="/sitio/index.php?mid=consultas&id=66065341"),
        ]
        # Both IDs already in DB
        svc, _, mock_fetcher = _make_service(
            notifications=notifs,
            existing_ids={"66065340", "66065341"},
        )
        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(return_value=None),
        ):
            result = await svc.run_poll()

        assert result["skipped"] == 2
        assert result["new"] == 0

    @pytest.mark.asyncio
    async def test_mark_seen_still_called_for_duplicates(self):
        notifs = [_make_notification(notif_id=1001)]
        svc, _, mock_fetcher = _make_service(
            notifications=notifs,
            existing_ids={"66065340"},
        )
        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(return_value=None),
        ):
            await svc.run_poll()

        mock_fetcher.mark_seen.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_partial_dedup(self):
        """One new lead + one duplicate => new=1, skipped=1."""
        notifs = [
            _make_notification(notif_id=1001, url="/sitio/index.php?mid=consultas&id=66065340"),
            _make_notification(notif_id=1002, url="/sitio/index.php?mid=consultas&id=66065341"),
        ]
        # Only first ID already processed
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_scalar_none_result())
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()

        svc, _, _ = _make_service(
            notifications=notifs,
            existing_ids={"66065340"},
            lead_data={
                "id": "66065341",
                "message": "Me interesa",
                "created_at": "2026-03-28 14:30:00",
                "from": {
                    "name": "Test User",
                    "email": "test@example.com",
                    "phone": "+595991111111",
                    "whatsapp_phone": None,
                    "has_whatsapp": False,
                },
                "listing": {
                    "id": "111",
                    "title": "Depto en Asuncion",
                    "code": "DEPTO01",
                    "neighborhood": {"name": "Asuncion"},
                },
            },
            session=mock_session,
        )
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.LeadEventRepository.create",
            new=AsyncMock(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(return_value=None),
        ):
            result = await svc.run_poll()

        assert result["new"] == 1
        assert result["skipped"] == 1


# ---------------------------------------------------------------------------
# TestMarkSeenForAll
# ---------------------------------------------------------------------------


class TestMarkSeenForAll:
    """mark_seen is called for every notification, including failures."""

    @pytest.mark.asyncio
    async def test_mark_seen_called_even_when_process_lead_raises(self):
        notif = _make_notification()
        svc, _, mock_fetcher = _make_service(
            notifications=[notif],
            # fetch_lead_details raises an exception mid-processing
        )
        mock_fetcher.fetch_lead_details = AsyncMock(side_effect=RuntimeError("boom"))
        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(return_value=None),
        ):
            result = await svc.run_poll()

        # Error is counted but mark_seen still called
        assert result["errors"] == 1
        mock_fetcher.mark_seen.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_errors_counted_per_lead(self):
        notifs = [
            _make_notification(notif_id=1, url="/sitio/index.php?mid=consultas&id=1"),
            _make_notification(notif_id=2, url="/sitio/index.php?mid=consultas&id=2"),
        ]
        svc, _, mock_fetcher = _make_service(notifications=notifs)
        mock_fetcher.fetch_lead_details = AsyncMock(side_effect=RuntimeError("fail"))
        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(return_value=None),
        ):
            result = await svc.run_poll()

        assert result["errors"] == 2
        assert mock_fetcher.mark_seen.await_count == 2


# ---------------------------------------------------------------------------
# TestProcessLead
# ---------------------------------------------------------------------------


class TestProcessLead:
    """_process_lead: full pipeline from consulta_id to stored contact."""

    @pytest.mark.asyncio
    async def test_returns_false_when_fetch_returns_none(self):
        svc, _, mock_fetcher = _make_service(lead_data=None)
        result = await svc._process_lead("token", "66065340")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_parse_returns_none(self):
        # lead with no phone AND no email -> parse_lead returns None
        svc, _, mock_fetcher = _make_service(
            lead_data={
                "id": "66065340",
                "message": None,
                "created_at": "2026-03-28 14:30:00",
                "from": {
                    "name": "Anon",
                    "email": None,
                    "phone": None,
                    "whatsapp_phone": None,
                    "has_whatsapp": False,
                },
                "listing": {},
            }
        )
        result = await svc._process_lead("token", "66065340")
        assert result is False

    @pytest.mark.asyncio
    async def test_descartado_escribe_dead_letter_con_el_consulta_id(self):
        """El descarte tiene que dejar un lead_event con el consulta_id adentro
        de metadata, porque es AHI donde el dedup lo va a buscar
        (check_existing_ids: metadata->>'consulta_id'). Sin esta fila los
        mismos 6 consulta_id vuelven a entrar cada 5 minutos, para siempre."""
        mock_session = AsyncMock()
        svc, _, _ = _make_service(
            session=mock_session,
            lead_data={
                "id": "69577799",
                "message": "Hola, vi esta propiedad en InfoCasas",
                "created_at": "2026-08-23 19:49:20",
                # El caso real: InfoCasas partio "Elisa Gill" y le puso el
                # apellido en el campo phone.
                "from": {
                    "name": "Elisa", "email": None, "phone": "Gill",
                    "whatsapp_phone": None, "has_whatsapp": False,
                },
                "listing": {"title": "ALQUILER - DPTO. 2 DORM.", "code": "R8954D"},
            },
        )
        with patch(
            "app.bot.services.infocasas.infocasas_service.LeadEventRepository.create",
            new=AsyncMock(),
        ) as mock_create:
            result = await svc._process_lead("token", "69577799")

        assert result is False
        assert mock_create.await_count == 1, (
            "el descarte tiene que escribir exactamente un dead-letter"
        )
        kwargs = mock_create.await_args.kwargs
        assert kwargs["contact_id"] is None
        assert kwargs["event_type"] == "discarded_no_contact"
        assert kwargs["metadata"]["consulta_id"] == "69577799"
        assert kwargs["metadata"]["discard_reason"] == "no_phone_no_email"
        # El payload crudo se guarda entero: es lo unico que permite recuperar
        # despues al cliente cuyo apellido quedo en el campo phone.
        assert kwargs["metadata"]["raw_from"] == {
            "name": "Elisa", "email": None, "phone": "Gill",
            "whatsapp_phone": None, "has_whatsapp": False,
        }
        assert kwargs["metadata"]["property_code"] == "R8954D"

    @pytest.mark.asyncio
    async def test_dead_letter_roto_no_tumba_el_poll(self):
        """Si la escritura del dead-letter falla, _process_lead devuelve False
        sin propagar: un rastro roto no puede matar la captura de los buenos."""
        svc, _, _ = _make_service(
            session=AsyncMock(),
            lead_data={
                "id": "69469835", "message": "Tengo interes",
                "created_at": "2026-08-19 21:08:47",
                "from": None, "listing": {},
            },
        )
        with patch(
            "app.bot.services.infocasas.infocasas_service.LeadEventRepository.create",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await svc._process_lead("token", "69469835")
        assert result is False

    @pytest.mark.asyncio
    async def test_lead_bueno_no_escribe_dead_letter(self):
        """La contracara: un lead con telefono NO pasa por el dead-letter.

        Sin este caso, un `_log_discarded_lead` llamado siempre pasaria el test
        de arriba igual.
        """
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_scalar_none_result())
        svc, _, _ = _make_service(
            session=mock_session,
            lead_data={
                "id": "66065340", "message": "Me interesa",
                "created_at": "2026-03-28 14:30:00",
                "from": {
                    "name": "Nicole", "email": None, "phone": "+595900000001",
                    "whatsapp_phone": None, "has_whatsapp": False,
                },
                "listing": {"title": "Casa", "code": "OF23CE"},
            },
        )
        with patch.object(
            svc, "_log_discarded_lead", new=AsyncMock()
        ) as mock_dl:
            await svc._process_lead("token", "66065340")
        assert mock_dl.await_count == 0

    @pytest.mark.asyncio
    async def test_returns_true_for_new_contact(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_scalar_none_result())
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()

        svc, _, _ = _make_service(
            lead_data={
                "id": "66065340",
                "message": "Me interesa",
                "created_at": "2026-03-28 14:30:00",
                "from": {
                    "name": "Nicole",
                    "email": "nicole@example.com",
                    "phone": "+595900000001",
                    "whatsapp_phone": None,
                    "has_whatsapp": False,
                },
                "listing": {
                    "id": "100",
                    "title": "Casa en Asuncion",
                    "code": "CASA01",
                    "neighborhood": {"name": "Asuncion"},
                },
            },
            session=mock_session,
        )
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.LeadEventRepository.create",
            new=AsyncMock(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(return_value=None),
        ):
            result = await svc._process_lead("token", "66065340")

        assert result is True


# ---------------------------------------------------------------------------
# TestUpsertContact
# ---------------------------------------------------------------------------


class TestUpsertContact:
    """_upsert_contact: new, existing by phone, existing by source_id."""

    @pytest.mark.asyncio
    async def test_creates_new_contact(self):
        parsed = _make_parsed_lead()
        mock_session = AsyncMock()
        # get_by_phone -> None, select by source_id -> None
        mock_session.execute = AsyncMock(return_value=_scalar_none_result())
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=None),
        ):
            is_new, is_new_property, contact = await svc._upsert_contact(mock_session, parsed)

        assert is_new is True
        assert is_new_property is True
        assert contact is not None
        assert contact.source == "infocasas"
        assert contact.source_id == parsed.consulta_id
        assert contact.phone == parsed.phone
        assert contact.status == "new"
        mock_session.add.assert_called_once_with(contact)

    @pytest.mark.asyncio
    async def test_new_contact_fields_set_correctly(self):
        parsed = _make_parsed_lead()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_scalar_none_result())
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=None),
        ):
            _, __, contact = await svc._upsert_contact(mock_session, parsed)

        assert contact.name == parsed.name
        assert contact.email == parsed.email
        assert contact.infocasas_ref == parsed.property_code
        assert contact.first_message == parsed.message
        assert contact.consulta_date == parsed.consulta_date

    @pytest.mark.asyncio
    async def test_existing_by_phone_returns_is_new_false(self):
        parsed = _make_parsed_lead()
        existing_contact = MagicMock(
            spec=["source_id", "infocasas_ref", "updated_at", "last_activity_at",
                  "preferences", "status", "baja_at", "id", "consulta_date"]
        )
        existing_contact.source_id = None
        existing_contact.infocasas_ref = None  # same as parsed.property_code → is_new_property=False
        existing_contact.updated_at = None
        existing_contact.last_activity_at = None
        existing_contact.preferences = None
        existing_contact.status = "no_response"
        existing_contact.baja_at = None
        existing_contact.id = 1
        existing_contact.consulta_date = None

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=existing_contact),
        ):
            is_new, is_new_property, contact = await svc._upsert_contact(mock_session, parsed)

        assert is_new is False
        assert contact is existing_contact
        assert contact.source_id == parsed.consulta_id
        assert contact.infocasas_ref == parsed.property_code

    @pytest.mark.asyncio
    async def test_existing_by_phone_different_property_returns_is_new_property_true(self):
        """Same phone but different property code → is_new_property=True (new inquiry)."""
        parsed = _make_parsed_lead(property_code="NEW_REF")
        existing_contact = MagicMock(
            spec=["source_id", "infocasas_ref", "updated_at", "last_activity_at",
                  "preferences", "status", "baja_at", "id", "consulta_date"]
        )
        existing_contact.source_id = None
        existing_contact.infocasas_ref = "OLD_REF"
        existing_contact.updated_at = None
        existing_contact.last_activity_at = None
        existing_contact.preferences = {}
        existing_contact.status = "no_response"
        existing_contact.baja_at = None
        existing_contact.id = 1
        existing_contact.consulta_date = None

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_ic_result = MagicMock()
        mock_ic_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_ic_result)

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=existing_contact),
        ):
            is_new, is_new_property, contact = await svc._upsert_contact(mock_session, parsed)

        assert is_new is False
        assert is_new_property is True
        assert contact is existing_contact

    @pytest.mark.asyncio
    async def test_existing_by_phone_same_property_returns_is_new_property_false(self):
        """Same phone and same property code → is_new_property=False (true duplicate)."""
        parsed = _make_parsed_lead(property_code="SAME_REF")
        existing_contact = MagicMock(
            spec=["source_id", "infocasas_ref", "updated_at", "last_activity_at",
                  "preferences", "status", "baja_at", "id", "consulta_date"]
        )
        existing_contact.source_id = None
        existing_contact.infocasas_ref = "SAME_REF"
        existing_contact.updated_at = None
        existing_contact.last_activity_at = None
        existing_contact.preferences = {}
        existing_contact.status = "no_response"
        existing_contact.baja_at = None
        existing_contact.id = 1
        existing_contact.consulta_date = None

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=existing_contact),
        ):
            is_new, is_new_property, contact = await svc._upsert_contact(mock_session, parsed)

        assert is_new is False
        assert is_new_property is False
        assert contact is existing_contact

    @pytest.mark.asyncio
    async def test_existing_by_source_id_returns_is_new_false(self):
        parsed = _make_parsed_lead(phone=None, email="only@email.com")
        existing_contact = MagicMock()
        existing_contact.updated_at = None
        existing_contact.last_activity_at = None

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_scalar_result(existing_contact))
        mock_session.flush = AsyncMock()

        svc, _, _ = _make_service()
        # No phone => get_by_phone is skipped
        is_new, is_new_property, contact = await svc._upsert_contact(mock_session, parsed)

        assert is_new is False
        assert is_new_property is False
        assert contact is existing_contact

    @pytest.mark.asyncio
    async def test_no_session_add_when_existing_by_phone(self):
        parsed = _make_parsed_lead()
        existing_contact = MagicMock()
        existing_contact.source_id = None
        existing_contact.infocasas_ref = None
        existing_contact.updated_at = None
        existing_contact.last_activity_at = None

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=existing_contact),
        ):
            await svc._upsert_contact(mock_session, parsed)

        mock_session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_saves_history_before_overwrite(self):
        """Existing contact, new property -> history row saved BEFORE infocasas_ref overwritten."""
        parsed = _make_parsed_lead(property_code="NEW_REF")
        existing_contact = MagicMock(
            spec=["id", "source_id", "infocasas_ref", "updated_at", "last_activity_at",
                  "preferences", "status", "baja_at", "consulta_date"]
        )
        existing_contact.id = 42
        existing_contact.source_id = "OLD_CONSULTA"
        existing_contact.infocasas_ref = "OLD_REF"
        existing_contact.consulta_date = datetime(2026, 3, 1, tzinfo=timezone.utc)
        existing_contact.updated_at = None
        existing_contact.last_activity_at = None
        existing_contact.preferences = {}
        existing_contact.status = "no_response"
        existing_contact.baja_at = None

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_ic_result = MagicMock()
        mock_ic_result.scalar_one_or_none.return_value = "Casa Antigua en Luque"
        mock_session.execute = AsyncMock(return_value=mock_ic_result)

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=existing_contact),
        ):
            is_new, is_new_property, contact = await svc._upsert_contact(mock_session, parsed)

        assert is_new is False
        assert is_new_property is True
        from app.models.inquiry_history import InquiryHistory
        add_calls = mock_session.add.call_args_list
        history_adds = [c for c in add_calls if isinstance(c[0][0], InquiryHistory)]
        assert len(history_adds) == 1
        h = history_adds[0][0][0]
        assert h.contact_id == 42
        assert h.infocasas_ref == "OLD_REF"
        assert h.consulta_id == "OLD_CONSULTA"
        assert h.property_title == "Casa Antigua en Luque"
        assert contact.infocasas_ref == parsed.property_code

    @pytest.mark.asyncio
    async def test_upsert_resets_status_no_response(self):
        """Contact in no_response + new property + no baja_at -> status reset to new."""
        parsed = _make_parsed_lead(property_code="NEW_REF")
        existing_contact = MagicMock(
            spec=["id", "source_id", "infocasas_ref", "updated_at", "last_activity_at",
                  "preferences", "status", "baja_at", "consulta_date"]
        )
        existing_contact.id = 42
        existing_contact.source_id = "OLD_C"
        existing_contact.infocasas_ref = "OLD_REF"
        existing_contact.consulta_date = None
        existing_contact.updated_at = None
        existing_contact.last_activity_at = None
        existing_contact.preferences = {}
        existing_contact.status = "no_response"
        existing_contact.baja_at = None

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=existing_contact),
        ):
            await svc._upsert_contact(mock_session, parsed)

        assert existing_contact.status == "new"

    @pytest.mark.asyncio
    async def test_upsert_resets_status_bot_replied(self):
        """Contact in bot_replied + new property + no baja_at -> status reset to new."""
        parsed = _make_parsed_lead(property_code="NEW_REF")
        existing_contact = MagicMock(
            spec=["id", "source_id", "infocasas_ref", "updated_at", "last_activity_at",
                  "preferences", "status", "baja_at", "consulta_date"]
        )
        existing_contact.id = 42
        existing_contact.source_id = "OLD_C"
        existing_contact.infocasas_ref = "OLD_REF"
        existing_contact.consulta_date = None
        existing_contact.updated_at = None
        existing_contact.last_activity_at = None
        existing_contact.preferences = {}
        existing_contact.status = "bot_replied"
        existing_contact.baja_at = None

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=existing_contact),
        ):
            await svc._upsert_contact(mock_session, parsed)

        assert existing_contact.status == "new"

    @pytest.mark.asyncio
    async def test_upsert_resets_status_discarded_no_baja(self):
        """Contact in discarded WITHOUT baja_at + new property -> status reset to new."""
        parsed = _make_parsed_lead(property_code="NEW_REF")
        existing_contact = MagicMock(
            spec=["id", "source_id", "infocasas_ref", "updated_at", "last_activity_at",
                  "preferences", "status", "baja_at", "consulta_date"]
        )
        existing_contact.id = 42
        existing_contact.source_id = "OLD_C"
        existing_contact.infocasas_ref = "OLD_REF"
        existing_contact.consulta_date = None
        existing_contact.updated_at = None
        existing_contact.last_activity_at = None
        existing_contact.preferences = {}
        existing_contact.status = "discarded"
        existing_contact.baja_at = None

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=existing_contact),
        ):
            await svc._upsert_contact(mock_session, parsed)

        assert existing_contact.status == "new"

    @pytest.mark.asyncio
    async def test_upsert_no_reset_interested(self):
        """Contact in interested + new property -> status stays interested."""
        parsed = _make_parsed_lead(property_code="NEW_REF")
        existing_contact = MagicMock(
            spec=["id", "source_id", "infocasas_ref", "updated_at", "last_activity_at",
                  "preferences", "status", "baja_at", "consulta_date"]
        )
        existing_contact.id = 42
        existing_contact.source_id = "OLD_C"
        existing_contact.infocasas_ref = "OLD_REF"
        existing_contact.consulta_date = None
        existing_contact.updated_at = None
        existing_contact.last_activity_at = None
        existing_contact.preferences = {}
        existing_contact.status = "interested"
        existing_contact.baja_at = None

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=existing_contact),
        ):
            await svc._upsert_contact(mock_session, parsed)

        assert existing_contact.status == "interested"

    @pytest.mark.asyncio
    async def test_upsert_no_reset_closed(self):
        """Contact in closed + new property -> status stays closed."""
        parsed = _make_parsed_lead(property_code="NEW_REF")
        existing_contact = MagicMock(
            spec=["id", "source_id", "infocasas_ref", "updated_at", "last_activity_at",
                  "preferences", "status", "baja_at", "consulta_date"]
        )
        existing_contact.id = 42
        existing_contact.source_id = "OLD_C"
        existing_contact.infocasas_ref = "OLD_REF"
        existing_contact.consulta_date = None
        existing_contact.updated_at = None
        existing_contact.last_activity_at = None
        existing_contact.preferences = {}
        existing_contact.status = "closed"
        existing_contact.baja_at = None

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=existing_contact),
        ):
            await svc._upsert_contact(mock_session, parsed)

        assert existing_contact.status == "closed"

    @pytest.mark.asyncio
    async def test_upsert_no_reset_optout(self):
        """Contact discarded WITH baja_at + new property -> status stays, history still saved."""
        from datetime import timezone as tz
        parsed = _make_parsed_lead(property_code="NEW_REF")
        existing_contact = MagicMock(
            spec=["id", "source_id", "infocasas_ref", "updated_at", "last_activity_at",
                  "preferences", "status", "baja_at", "consulta_date"]
        )
        existing_contact.id = 42
        existing_contact.source_id = "OLD_C"
        existing_contact.infocasas_ref = "OLD_REF"
        existing_contact.consulta_date = None
        existing_contact.updated_at = None
        existing_contact.last_activity_at = None
        existing_contact.preferences = {}
        existing_contact.status = "discarded"
        existing_contact.baja_at = datetime(2026, 1, 1, tzinfo=tz.utc)

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=existing_contact),
        ):
            await svc._upsert_contact(mock_session, parsed)

        assert existing_contact.status == "discarded"
        from app.models.inquiry_history import InquiryHistory
        add_calls = mock_session.add.call_args_list
        history_adds = [c for c in add_calls if isinstance(c[0][0], InquiryHistory)]
        assert len(history_adds) == 1

    @pytest.mark.asyncio
    async def test_upsert_no_reset_agent_replied(self):
        """Contact in agent_replied + new property -> status stays agent_replied."""
        parsed = _make_parsed_lead(property_code="NEW_REF")
        existing_contact = MagicMock(
            spec=["id", "source_id", "infocasas_ref", "updated_at", "last_activity_at",
                  "preferences", "status", "baja_at", "consulta_date"]
        )
        existing_contact.id = 42
        existing_contact.source_id = "OLD_C"
        existing_contact.infocasas_ref = "OLD_REF"
        existing_contact.consulta_date = None
        existing_contact.updated_at = None
        existing_contact.last_activity_at = None
        existing_contact.preferences = {}
        existing_contact.status = "agent_replied"
        existing_contact.baja_at = None

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=existing_contact),
        ):
            await svc._upsert_contact(mock_session, parsed)

        assert existing_contact.status == "agent_replied"

    @pytest.mark.asyncio
    async def test_upsert_same_property_no_history(self):
        """Same property (is_new_property=False) -> NO history entry."""
        parsed = _make_parsed_lead(property_code="SAME_REF")
        existing_contact = MagicMock(
            spec=["id", "source_id", "infocasas_ref", "updated_at", "last_activity_at",
                  "preferences", "status", "baja_at", "consulta_date"]
        )
        existing_contact.id = 42
        existing_contact.source_id = "OLD_C"
        existing_contact.infocasas_ref = "SAME_REF"
        existing_contact.consulta_date = None
        existing_contact.updated_at = None
        existing_contact.last_activity_at = None
        existing_contact.preferences = {}
        existing_contact.status = "no_response"
        existing_contact.baja_at = None

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.ContactRepository.get_by_phone",
            new=AsyncMock(return_value=existing_contact),
        ):
            await svc._upsert_contact(mock_session, parsed)

        mock_session.add.assert_not_called()


# ---------------------------------------------------------------------------
# TestNotifyNewLeadHeader
# ---------------------------------------------------------------------------


class TestNotifyNewLeadHeader:
    """Verify _notify_new_lead produces the correct Telegram header per case."""

    def _make_parsed_lead_for_notify(self):
        """Minimal ParsedLead for notification tests."""
        return _make_parsed_lead()

    @pytest.mark.asyncio
    async def test_header_new_lead(self):
        """is_recurring=False -> NUEVO LEAD INFOCASAS header."""
        svc, _, _ = _make_service()
        parsed = self._make_parsed_lead_for_notify()

        await svc._notify_new_lead(parsed, None, is_recurring=False, contact_status="new", is_optout=False)

        assert svc._notifier.notify.called
        call_text = svc._notifier.notify.call_args.args[0]
        assert "NUEVO LEAD INFOCASAS" in call_text

    @pytest.mark.asyncio
    async def test_header_recurring_optout(self):
        """is_recurring=True, is_optout=True -> LEAD RECURRENTE IC (opt-out) header."""
        svc, _, _ = _make_service()
        parsed = self._make_parsed_lead_for_notify()

        await svc._notify_new_lead(parsed, None, is_recurring=True, contact_status="discarded", is_optout=True)

        assert svc._notifier.notify.called
        call_text = svc._notifier.notify.call_args.args[0]
        assert "opt-out" in call_text

    @pytest.mark.asyncio
    async def test_header_recurring_reactivated(self):
        """is_recurring=True, contact_status='new' (reset from no_response) -> reactivado header."""
        svc, _, _ = _make_service()
        parsed = self._make_parsed_lead_for_notify()

        await svc._notify_new_lead(parsed, None, is_recurring=True, contact_status="new", is_optout=False)

        assert svc._notifier.notify.called
        call_text = svc._notifier.notify.call_args.args[0]
        assert "reactivado" in call_text

    @pytest.mark.asyncio
    async def test_header_recurring_active(self):
        """is_recurring=True, contact_status='interested' -> LEAD RECURRENTE IC (activo: interested) header."""
        svc, _, _ = _make_service()
        parsed = self._make_parsed_lead_for_notify()

        await svc._notify_new_lead(parsed, None, is_recurring=True, contact_status="interested", is_optout=False)

        assert svc._notifier.notify.called
        call_text = svc._notifier.notify.call_args.args[0]
        assert "activo" in call_text
        assert "interested" in call_text


# ---------------------------------------------------------------------------
# TestMatchProperty
# ---------------------------------------------------------------------------


class TestMatchProperty:
    """_match_property: returns IC info for logging only, never writes contacts.property_id."""

    @pytest.mark.asyncio
    async def test_match_property_no_code_returns_none(self):
        """_match_property returns None when no property_code."""
        svc = InfocasasService.__new__(InfocasasService)
        parsed = MagicMock()
        parsed.property_code = None

        result = await svc._match_property(MagicMock(), parsed, contact_id=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_match_property_returns_ic_info_without_setting_property_id(self):
        """_match_property returns IC info but does NOT update contact.property_id."""
        svc = InfocasasService.__new__(InfocasasService)
        parsed = MagicMock()
        parsed.property_code = "ICCBFD"
        ic_prop = MagicMock()
        ic_prop.city = "Asuncion"
        ic_prop.title = "VENDE TERRENO"

        mock_session = MagicMock()

        with patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=ic_prop),
        ):
            result = await svc._match_property(mock_session, parsed, contact_id=99)

        assert result == {"city": "Asuncion", "title": "VENDE TERRENO", "matched_by": "infocasas_ref"}
        # Must NOT write to contacts table
        mock_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_match_property_unknown_ref_returns_none(self):
        """_match_property returns None when infocasas_ref not found."""
        svc = InfocasasService.__new__(InfocasasService)
        parsed = MagicMock()
        parsed.property_code = "UNKNOWN"

        mock_session = MagicMock()

        with patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=None),
        ):
            result = await svc._match_property(mock_session, parsed, contact_id=1)

        assert result is None


# ---------------------------------------------------------------------------
# TestLogLeadEvent
# ---------------------------------------------------------------------------


class TestLogLeadEvent:
    """_log_lead_event: creates event with correct metadata."""

    @pytest.mark.asyncio
    async def test_creates_event_with_consulta_id_in_metadata(self):
        parsed = _make_parsed_lead()
        mock_session = AsyncMock()

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.LeadEventRepository.create",
            new=AsyncMock(),
        ) as mock_create:
            await svc._log_lead_event(mock_session, 1, "created", parsed, None)
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["metadata"]["consulta_id"] == parsed.consulta_id
            assert call_kwargs["metadata"]["source"] == "infocasas"

    @pytest.mark.asyncio
    async def test_event_type_created_sets_new_status(self):
        parsed = _make_parsed_lead()
        mock_session = AsyncMock()

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.LeadEventRepository.create",
            new=AsyncMock(),
        ) as mock_create:
            await svc._log_lead_event(mock_session, 1, "created", parsed, None)
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["new_status"] == "new"
            assert call_kwargs["triggered_by"] == "infocasas_poll"

    @pytest.mark.asyncio
    async def test_event_type_linked_existing_has_no_new_status(self):
        parsed = _make_parsed_lead()
        mock_session = AsyncMock()

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.LeadEventRepository.create",
            new=AsyncMock(),
        ) as mock_create:
            await svc._log_lead_event(
                mock_session, 1, "linked_existing", parsed, None
            )
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["new_status"] is None

    @pytest.mark.asyncio
    async def test_event_type_new_inquiry_has_no_new_status(self):
        """new_inquiry (same contact, different property) has no status change."""
        parsed = _make_parsed_lead()
        mock_session = AsyncMock()

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.LeadEventRepository.create",
            new=AsyncMock(),
        ) as mock_create:
            await svc._log_lead_event(
                mock_session, 1, "new_inquiry", parsed, None
            )
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["new_status"] is None
            assert call_kwargs["event_type"] == "new_inquiry"

    @pytest.mark.asyncio
    async def test_matched_property_id_in_metadata(self):
        parsed = _make_parsed_lead()
        mock_session = AsyncMock()
        matched = {"id": 42, "city": "Asuncion", "matched_by": "external_id"}

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.LeadEventRepository.create",
            new=AsyncMock(),
        ) as mock_create:
            await svc._log_lead_event(mock_session, 1, "created", parsed, matched)
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["metadata"]["matched_property_id"] == 42
            assert call_kwargs["metadata"]["matched_by"] == "external_id"

    # ---------------- M6.0 / CLEAN-03 — reenviado sub-key ----------------

    @pytest.mark.asyncio
    async def test_log_lead_event_persists_reassigned_fields(self):
        """is_reassigned leads must persist all 7 parsed listing fields
        under metadata['reenviado'] sub-key (blueprint Q1 decision)."""
        parsed = _make_parsed_lead(is_reassigned=True)
        parsed.listing_type = "casa"
        parsed.listing_operation = "venta"
        parsed.listing_bedrooms = 3
        parsed.listing_area_m2 = 180.0
        parsed.listing_price = 120000.0
        parsed.listing_currency = "usd"
        parsed.listing_zone_from_message = "Villa Morra"
        mock_session = AsyncMock()

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.LeadEventRepository.create",
            new=AsyncMock(),
        ) as mock_create:
            await svc._log_lead_event(mock_session, 1, "created", parsed, None)
            metadata = mock_create.call_args.kwargs["metadata"]
            assert "reenviado" in metadata
            reenviado = metadata["reenviado"]
            assert reenviado["listing_type"] == "casa"
            assert reenviado["listing_operation"] == "venta"
            assert reenviado["listing_bedrooms"] == 3
            assert reenviado["listing_area_m2"] == 180.0
            assert reenviado["listing_price"] == 120000.0
            assert reenviado["listing_currency"] == "usd"
            assert reenviado["listing_zone_from_message"] == "Villa Morra"

    @pytest.mark.asyncio
    async def test_log_lead_event_skips_reenviado_key_for_non_reassigned(self):
        """Non-reassigned leads must NOT contain a 'reenviado' key — keep
        metadata payloads narrow for the common path."""
        parsed = _make_parsed_lead(is_reassigned=False)
        mock_session = AsyncMock()

        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.LeadEventRepository.create",
            new=AsyncMock(),
        ) as mock_create:
            await svc._log_lead_event(mock_session, 1, "created", parsed, None)
            metadata = mock_create.call_args.kwargs["metadata"]
            assert "reenviado" not in metadata


# ---------------------------------------------------------------------------
# TestNotifyNewLead
# ---------------------------------------------------------------------------


class TestNotifyNewLead:
    """_notify_new_lead: Telegram notification format, no notifier, swallows errors."""

    @pytest.mark.asyncio
    async def test_sends_notification_with_name(self):
        parsed = _make_parsed_lead()
        mock_notifier = AsyncMock()
        mock_notifier.notify = AsyncMock(return_value=True)

        svc = InfocasasService(
            session_manager=AsyncMock(),
            notification_fetcher=AsyncMock(),
            notifier=mock_notifier,
            session_factory=_make_session_factory(),
        )
        await svc._notify_new_lead(parsed, None)

        mock_notifier.notify.assert_awaited_once()
        msg = mock_notifier.notify.call_args.args[0]
        assert "Nicole Caceres" in msg
        assert "NUEVO LEAD INFOCASAS" in msg

    @pytest.mark.asyncio
    async def test_includes_phone_in_message(self):
        parsed = _make_parsed_lead()
        mock_notifier = AsyncMock()
        mock_notifier.notify = AsyncMock(return_value=True)

        svc = InfocasasService(
            session_manager=AsyncMock(),
            notification_fetcher=AsyncMock(),
            notifier=mock_notifier,
            session_factory=_make_session_factory(),
        )
        await svc._notify_new_lead(parsed, None)

        msg = mock_notifier.notify.call_args.args[0]
        assert "+595900000001" in msg

    @pytest.mark.asyncio
    async def test_skips_when_no_notifier(self):
        parsed = _make_parsed_lead()
        svc = InfocasasService(
            session_manager=AsyncMock(),
            notification_fetcher=AsyncMock(),
            notifier=None,
            session_factory=_make_session_factory(),
        )
        # Should not raise
        await svc._notify_new_lead(parsed, None)

    @pytest.mark.asyncio
    async def test_exception_is_swallowed(self):
        parsed = _make_parsed_lead()
        mock_notifier = AsyncMock()
        mock_notifier.notify = AsyncMock(side_effect=Exception("network failure"))

        svc = InfocasasService(
            session_manager=AsyncMock(),
            notification_fetcher=AsyncMock(),
            notifier=mock_notifier,
            session_factory=_make_session_factory(),
        )
        # Must not raise
        await svc._notify_new_lead(parsed, None)

    @pytest.mark.asyncio
    async def test_zone_from_matched_property(self):
        parsed = _make_parsed_lead(listing_city="Ciudad del Este")
        matched = {"id": 1, "city": "San Lorenzo", "matched_by": "external_id"}
        mock_notifier = AsyncMock()
        mock_notifier.notify = AsyncMock(return_value=True)

        svc = InfocasasService(
            session_manager=AsyncMock(),
            notification_fetcher=AsyncMock(),
            notifier=mock_notifier,
            session_factory=_make_session_factory(),
        )
        await svc._notify_new_lead(parsed, matched)

        msg = mock_notifier.notify.call_args.args[0]
        assert "San Lorenzo" in msg


# ---------------------------------------------------------------------------
# TestSendWhatsappWelcome
# ---------------------------------------------------------------------------


class TestSendWhatsappWelcome:
    """_send_whatsapp_welcome: template, no phone, no template, delay."""

    @pytest.mark.asyncio
    async def test_skips_when_no_phone(self):
        parsed = _make_parsed_lead(phone=None)
        svc, _, _ = _make_service()
        # Should return without doing anything
        await svc._send_whatsapp_welcome(parsed, None, contact_id=99)

    @pytest.mark.asyncio
    async def test_skips_when_no_template_sid(self):
        parsed = _make_parsed_lead()
        svc, _, _ = _make_service()
        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(return_value=None),
        ):
            # Should return without HTTP call
            await svc._send_whatsapp_welcome(parsed, None, contact_id=99)

    @pytest.mark.asyncio
    async def test_sends_template_with_correct_content_sid(self):
        parsed = _make_parsed_lead()
        template_sid = "HXabc123"

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        svc, _, _ = _make_service()

        async def fake_get_value(session, key):
            if key == "wa_tpl_ic_welcome":
                return template_sid
            if key == "infocasas_wa_delay_min":
                return "0"
            if key == "infocasas_wa_delay_max":
                return "0"
            return None

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=fake_get_value,
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch.object(
            svc, "_save_welcome_message", new=AsyncMock()
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("asyncio.sleep", new=AsyncMock()):
                await svc._send_whatsapp_welcome(parsed, None, contact_id=99)

        mock_http.post.assert_awaited_once()
        call_kwargs = mock_http.post.call_args
        data = call_kwargs.kwargs.get("data") or call_kwargs.args[1] if call_kwargs.args else call_kwargs.kwargs["data"]
        # The ContentSid must be the template SID
        assert data["ContentSid"] == template_sid

    @pytest.mark.asyncio
    async def test_content_variables_contain_name_and_zone(self):
        parsed = _make_parsed_lead(name="Nicole", listing_city="Asuncion")
        template_sid = "HXtest"

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        svc, _, _ = _make_service()

        async def fake_get_value(session, key):
            if key == "wa_tpl_ic_welcome":
                return template_sid
            if key in ("infocasas_wa_delay_min", "infocasas_wa_delay_max"):
                return "0"
            return None

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=fake_get_value,
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch.object(
            svc, "_save_welcome_message", new=AsyncMock()
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("asyncio.sleep", new=AsyncMock()):
                await svc._send_whatsapp_welcome(parsed, None, contact_id=99)

        call_kwargs = mock_http.post.call_args.kwargs
        content_vars = json.loads(call_kwargs["data"]["ContentVariables"])
        assert content_vars["1"] == "Nicole"
        assert content_vars["2"] == "Asuncion"

    @pytest.mark.asyncio
    async def test_delay_between_min_and_max(self):
        parsed = _make_parsed_lead()
        template_sid = "HXtest"

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        slept: list[float] = []

        async def capture_sleep(seconds: float) -> None:
            slept.append(seconds)

        svc, _, _ = _make_service()

        async def fake_get_value(session, key):
            if key == "wa_tpl_ic_welcome":
                return template_sid
            if key == "infocasas_wa_delay_min":
                return "2"
            if key == "infocasas_wa_delay_max":
                return "5"
            return None

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=fake_get_value,
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",  # patched via module-level asyncio import
            new=capture_sleep,
        ), patch.object(
            svc, "_save_welcome_message", new=AsyncMock()
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await svc._send_whatsapp_welcome(parsed, None, contact_id=99)

        assert len(slept) == 1
        assert 2.0 <= slept[0] <= 5.0

    @pytest.mark.asyncio
    async def test_http_error_does_not_raise(self):
        parsed = _make_parsed_lead()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=Exception("connection error"))

        svc, _, _ = _make_service()

        async def fake_get_value(session, key):
            if key == "wa_tpl_ic_welcome":
                return "HXtest"
            return "0"

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=fake_get_value,
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",  # patched via module-level asyncio import
            new=AsyncMock(),
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            # Must not raise
            await svc._send_whatsapp_welcome(parsed, None, contact_id=99)


# ---------------------------------------------------------------------------
# TestFactory
# ---------------------------------------------------------------------------


class TestFactory:
    """get_infocasas_service: factory returns correct type."""

    def test_returns_infocasas_service_instance(self):
        with patch(
            "app.bot.services.infocasas.session_manager.get_session_manager",
            return_value=MagicMock(spec=["get_valid_token"]),
        ), patch(
            "app.bot.services.admin_notifier.get_admin_notifier",
            return_value=MagicMock(spec=["notify"]),
        ):
            svc = get_infocasas_service()

        assert isinstance(svc, InfocasasService)

    def test_factory_wires_notifier(self):
        mock_notifier = MagicMock(spec=["notify"])
        with patch(
            "app.bot.services.infocasas.session_manager.get_session_manager",
            return_value=MagicMock(),
        ), patch(
            "app.bot.services.admin_notifier.get_admin_notifier",
            return_value=mock_notifier,
        ):
            svc = get_infocasas_service()

        assert svc._notifier is mock_notifier


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _scalar_none_result() -> MagicMock:
    """Mock execute() result that returns None from scalar_one_or_none()."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    return result


def _scalar_result(value: object) -> MagicMock:
    """Mock execute() result returning *value* from scalar_one_or_none()."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


# ---------------------------------------------------------------------------
# TestProcessLeadReenviada
# ---------------------------------------------------------------------------


class TestProcessLeadReenviada:
    """Tests that reenviada leads skip WA auto-reply."""

    def _make_service(self, session_factory):
        from unittest.mock import AsyncMock, MagicMock
        from app.bot.services.infocasas.infocasas_service import InfocasasService
        from app.bot.services.infocasas.notification_fetcher import NotificationFetcher
        from app.bot.services.infocasas.session_manager import SessionManager

        sm = MagicMock(spec=SessionManager)
        sm.get_valid_token = AsyncMock(return_value="tok")
        nf = MagicMock(spec=NotificationFetcher)
        return InfocasasService(
            session_manager=sm,
            notification_fetcher=nf,
            notifier=None,
            session_factory=session_factory,
        )

    @pytest.mark.asyncio
    async def test_directa_sends_wa(self) -> None:
        """Direct leads with a matched property call _send_whatsapp_welcome."""
        parsed = _make_parsed_lead(is_reassigned=False)
        svc = self._make_service(None)

        from unittest.mock import AsyncMock, patch
        with patch.object(svc, "_notify_new_lead", new_callable=AsyncMock) as mock_notify, \
             patch.object(svc, "_send_whatsapp_welcome", new_callable=AsyncMock) as mock_wa, \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert, \
             patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match, \
             patch.object(svc, "_log_lead_event", new_callable=AsyncMock):

            from app.models.contact import Contact
            from unittest.mock import MagicMock
            contact = MagicMock(spec=Contact)
            contact.id = 1
            mock_upsert.return_value = (True, True, contact)
            # matched_property is NOT None — direct lead with a match unconditionally
            # calls _send_whatsapp_welcome (no toggle check)
            mock_match.return_value = {"city": "Asuncion", "matched_by": "infocasas_ref"}

            from unittest.mock import AsyncMock as AM
            mock_session = MagicMock()
            mock_session.__aenter__ = AM(return_value=mock_session)
            mock_session.__aexit__ = AM(return_value=None)
            mock_session.commit = AM()
            svc._session_factory = MagicMock(return_value=mock_session)

            from app.bot.services.infocasas.lead_parser import parse_lead
            with patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed), \
                 patch.object(svc._fetcher, "fetch_lead_details", new_callable=AsyncMock, return_value={"id": "1"}):
                await svc._process_lead("tok", "1")

        mock_wa.assert_called_once()

    @pytest.mark.asyncio
    async def test_reenviada_skips_wa(self) -> None:
        """Reenviada leads do NOT call _send_whatsapp_welcome (uses reenviado path)."""
        parsed = _make_parsed_lead(is_reassigned=True)
        svc = self._make_service(None)

        from unittest.mock import AsyncMock, patch
        with patch.object(svc, "_notify_new_lead", new_callable=AsyncMock) as mock_notify, \
             patch.object(svc, "_send_whatsapp_welcome", new_callable=AsyncMock) as mock_wa, \
             patch.object(svc, "_send_whatsapp_reenviado_welcome", new_callable=AsyncMock) as mock_reenviado_wa, \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert, \
             patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match, \
             patch.object(svc, "_log_lead_event", new_callable=AsyncMock), \
             patch(
                 "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                 new=AsyncMock(return_value=None),  # toggle off
             ):

            from app.models.contact import Contact
            from unittest.mock import MagicMock
            contact = MagicMock(spec=Contact)
            contact.id = 1
            mock_upsert.return_value = (True, True, contact)
            mock_match.return_value = None

            from unittest.mock import AsyncMock as AM
            mock_session = MagicMock()
            mock_session.__aenter__ = AM(return_value=mock_session)
            mock_session.__aexit__ = AM(return_value=None)
            mock_session.commit = AM()
            svc._session_factory = MagicMock(return_value=mock_session)

            from app.bot.services.infocasas.lead_parser import parse_lead
            with patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed), \
                 patch.object(svc._fetcher, "fetch_lead_details", new_callable=AsyncMock, return_value={"id": "1"}):
                await svc._process_lead("tok", "1")

        mock_wa.assert_not_called()          # direct welcome never called for reenviados
        mock_reenviado_wa.assert_not_called()  # toggle off → reenviado welcome also skipped
        mock_notify.assert_called_once()  # Telegram still fires


# ---------------------------------------------------------------------------
# TestPreloadSearchContext
# ---------------------------------------------------------------------------


def _make_ic_prop(
    *,
    id: int = 9001,
    property_id: int | None = 42,
    city: str | None = "Luque",
    neighborhood: str | None = "Barrio Norte",
    operation: str = "venta",
    property_type: str = "casa",
    price_sale=None,
    price_rent=None,
    currency_sale: str | None = "USD",
    currency_rent: str | None = None,
    bedrooms: int | None = 3,
):
    """Build a minimal mock InfocasasProperty ORM object."""
    prop = MagicMock()
    prop.id = id
    prop.property_id = property_id
    prop.city = city
    prop.neighborhood = neighborhood
    prop.operation = operation
    prop.property_type = property_type
    prop.price_sale = price_sale
    prop.price_rent = price_rent
    prop.currency_sale = currency_sale
    prop.currency_rent = currency_rent
    prop.bedrooms = bedrooms
    return prop


class TestPreloadSearchContext:
    """_preload_search_context: populates filtros even when property_id IS NULL.

    Bug 2 root cause: the guard at infocasas_service.py:768 returned silently
    when ic_prop_full.property_id IS NULL, leaving search_context empty.
    Fix: use IC data to populate filtros regardless of property_id.
    """

    def _make_preload_service(self):
        factory = _make_session_factory()
        svc, _, _ = _make_service(session=factory._mock_return_value.__aenter__.return_value)
        svc._session_factory = factory
        return svc

    @pytest.mark.asyncio
    async def test_property_id_none_writes_filtros_from_ic_data(self):
        """When property_id IS NULL, update_search_context is called with IC filtros."""
        svc = self._make_preload_service()
        ic_prop = _make_ic_prop(
            property_id=None,
            city="San Lorenzo",
            neighborhood="Centro",
            operation="alquiler",
            property_type="departamento",
            price_rent=2_000_000,
            currency_rent="gs",
        )

        mock_conv_mgr = AsyncMock()
        mock_conv_mgr.get_or_create_conversation = AsyncMock(
            return_value=MagicMock(id=55)
        )
        mock_conv_mgr.update_search_context = AsyncMock()

        with patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager",
            return_value=mock_conv_mgr,
        ):
            await svc._preload_search_context(
                contact_id=1,
                phone="+595981000001",
                ic_prop_full=ic_prop,
            )

        mock_conv_mgr.update_search_context.assert_awaited_once()
        _, call_conv_id, saved_state = mock_conv_mgr.update_search_context.call_args[0]
        assert call_conv_id == 55
        assert saved_state.filtros.get("ciudad") == "San Lorenzo", (
            "filtros must include IC city when property_id IS NULL"
        )
        assert saved_state.filtros.get("operacion") == "alquiler"
        assert saved_state.filtros.get("tipo") == "departamento"
        assert saved_state.last_detalle_id is None, (
            "last_detalle_id must remain None when property_id IS NULL"
        )
        assert saved_state.last_ic_prop_id == ic_prop.id, (
            "last_ic_prop_id must be set to ic_prop.id when property_id IS NULL"
        )

    @pytest.mark.asyncio
    async def test_property_id_none_empty_city_no_crash(self):
        """When property_id IS NULL and city/neighborhood empty, no crash and context written."""
        svc = self._make_preload_service()
        ic_prop = _make_ic_prop(
            property_id=None,
            city=None,
            neighborhood=None,
            operation="venta",
            property_type="terreno",
        )

        mock_conv_mgr = AsyncMock()
        mock_conv_mgr.get_or_create_conversation = AsyncMock(
            return_value=MagicMock(id=56)
        )
        mock_conv_mgr.update_search_context = AsyncMock()

        with patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager",
            return_value=mock_conv_mgr,
        ):
            await svc._preload_search_context(
                contact_id=2,
                phone="+595982000002",
                ic_prop_full=ic_prop,
            )

        # Must still call update_search_context (not skip silently)
        mock_conv_mgr.update_search_context.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_property_id_valid_sets_last_detalle_id(self):
        """When property_id IS NOT NULL, last_detalle_id = property_id (path feliz)."""
        svc = self._make_preload_service()
        ic_prop = _make_ic_prop(
            property_id=99,
            city="Asuncion",
            operation="venta",
            property_type="casa",
            price_sale=250_000,
            currency_sale="USD",
        )

        mock_conv_mgr = AsyncMock()
        mock_conv_mgr.get_or_create_conversation = AsyncMock(
            return_value=MagicMock(id=57)
        )
        mock_conv_mgr.update_search_context = AsyncMock()

        with patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager",
            return_value=mock_conv_mgr,
        ):
            await svc._preload_search_context(
                contact_id=3,
                phone="+595983000003",
                ic_prop_full=ic_prop,
            )

        _, _, saved_state = mock_conv_mgr.update_search_context.call_args[0]
        assert saved_state.last_detalle_id == 99
        assert saved_state.etapa == "viendo_detalle"
        assert saved_state.last_ic_prop_id == ic_prop.id, (
            "last_ic_prop_id must be set to ic_prop.id when property_id is set"
        )


# ---------------------------------------------------------------------------
# Fase 5: 24h dedup window for phone+property duplicate IC leads
# ---------------------------------------------------------------------------


def _make_existing_contact(*, contact_id: int = 77) -> MagicMock:
    """Return a minimal mock Contact for (is_new=False, is_new_property=False) scenarios."""
    from app.models.contact import Contact

    c = MagicMock(spec=Contact)
    c.id = contact_id
    c.phone = "+595992000001"
    c.status = "no_response"
    c.baja_at = None
    return c


def _make_process_lead_service_and_session():
    """Build service + reusable async context manager session for _process_lead tests."""
    from unittest.mock import AsyncMock as AM, MagicMock as MM

    mock_session = MM()
    mock_session.__aenter__ = AM(return_value=mock_session)
    mock_session.__aexit__ = AM(return_value=None)
    mock_session.commit = AM()

    svc, _, _ = _make_service()
    svc._session_factory = MM(return_value=mock_session)
    return svc, mock_session


class TestFase5DedupWindow:
    """24h dedup: same phone+property within 24h skips template; beyond 24h promotes to recurrente."""

    # ------------------------------------------------------------------
    # Test 1: duplicate within 24h — template suppressed
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_duplicate_phone_property_within_24h_skips_template(self):
        """(is_new=False, is_new_property=False) + recent IC event → no WA template sent."""
        from unittest.mock import AsyncMock, MagicMock, patch

        parsed = _make_parsed_lead(property_code="OF23CE")
        contact = _make_existing_contact()
        svc, mock_session = _make_process_lead_service_and_session()

        mock_log = AsyncMock()
        mock_recurrente = AsyncMock()

        with patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed), \
             patch.object(svc._fetcher, "fetch_lead_details", new_callable=AsyncMock, return_value={"id": "1"}), \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock,
                          return_value=(False, False, contact)), \
             patch.object(svc, "_match_property", new_callable=AsyncMock, return_value=None), \
             patch.object(svc, "_log_lead_event", mock_log), \
             patch.object(svc, "_has_recent_ic_event", new_callable=AsyncMock, return_value=True), \
             patch.object(svc, "_send_whatsapp_recurrente_directo", mock_recurrente), \
             patch.object(svc, "_notify_new_lead", new_callable=AsyncMock):
            await svc._process_lead("tok", "66065340")

        # Template must NOT be sent
        mock_recurrente.assert_not_called()

        # _log_lead_event must have been called with skipped_reason="ic_dedup_within_24h"
        assert mock_log.call_count == 1
        call_kwargs = mock_log.call_args
        assert call_kwargs.kwargs["skipped_reason"] == "ic_dedup_within_24h", (
            f"Expected skipped_reason='ic_dedup_within_24h', got: {call_kwargs}"
        )

    # ------------------------------------------------------------------
    # Test 2: same phone+property but beyond 24h — promoted to recurrente
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_duplicate_phone_property_beyond_24h_sends_recurrente(self):
        """(is_new=False, is_new_property=False) + NO recent IC event → promoted, WA sent."""
        from unittest.mock import AsyncMock, MagicMock, patch

        parsed = _make_parsed_lead(property_code="OF23CE", is_reassigned=False)
        contact = _make_existing_contact()
        svc, mock_session = _make_process_lead_service_and_session()

        mock_log = AsyncMock()
        mock_recurrente = AsyncMock()

        with patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed), \
             patch.object(svc._fetcher, "fetch_lead_details", new_callable=AsyncMock, return_value={"id": "1"}), \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock,
                          return_value=(False, False, contact)), \
             patch.object(svc, "_match_property", new_callable=AsyncMock, return_value=None), \
             patch.object(svc, "_log_lead_event", mock_log), \
             patch.object(svc, "_has_recent_ic_event", new_callable=AsyncMock, return_value=False), \
             patch.object(svc, "_send_whatsapp_recurrente_directo", mock_recurrente), \
             patch.object(svc, "_notify_new_lead", new_callable=AsyncMock), \
             patch(
                 "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                 new=AsyncMock(return_value="true"),
             ):
            await svc._process_lead("tok", "66065340")

        # Promoted to recurrente — WA template must be sent
        mock_recurrente.assert_called_once()

        # event_type logged must be "new_inquiry" (the promoted branch)
        assert mock_log.call_count == 1
        # positional arg 2 is event_type
        event_type = mock_log.call_args.args[2] if len(mock_log.call_args.args) > 2 else mock_log.call_args.kwargs.get("event_type")
        assert event_type == "new_inquiry", (
            f"Expected event_type='new_inquiry' after promotion, got: {event_type}"
        )

    # ------------------------------------------------------------------
    # Test 3: _has_recent_ic_event unit test against raw SQL logic
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_has_recent_ic_event_query_respects_property_code_and_window(self):
        """_has_recent_ic_event returns True/False based on property_code and time window."""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import AsyncMock, MagicMock

        svc, _ = _make_process_lead_service_and_session()

        # --- Case A: matching row exists within 2h (should return True) ---
        mock_session_a = MagicMock()
        # Simulate scalar() returning 1 (a row exists)
        mock_result_a = MagicMock()
        mock_result_a.scalar.return_value = 1
        mock_session_a.execute = AsyncMock(return_value=mock_result_a)

        result_a = await svc._has_recent_ic_event(mock_session_a, 42, "PROP_X", within_hours=24)
        assert result_a is True, "Should return True when a matching row exists within window"

        # --- Case B: no row in window (scalar returns None) → False ---
        mock_session_b = MagicMock()
        mock_result_b = MagicMock()
        mock_result_b.scalar.return_value = None
        mock_session_b.execute = AsyncMock(return_value=mock_result_b)

        result_b = await svc._has_recent_ic_event(mock_session_b, 42, "PROP_X", within_hours=24)
        assert result_b is False, "Should return False when no matching row in window"

        # --- Case C: scalar returns 0 → False (no matches even if query ran) ---
        mock_session_c = MagicMock()
        mock_result_c = MagicMock()
        mock_result_c.scalar.return_value = 0
        mock_session_c.execute = AsyncMock(return_value=mock_result_c)

        result_c = await svc._has_recent_ic_event(mock_session_c, 42, "PROP_X", within_hours=24)
        assert result_c is False, "Should return False when scalar returns 0"

        # --- (a) SQL text assertions: verify the issued SQL contains the expected predicates ---
        issued_sql = mock_session_c.execute.call_args.args[0].text
        assert "event_type IN ('created', 'new_inquiry')" in issued_sql, (
            f"SQL missing event_type predicate: {issued_sql}"
        )
        assert "metadata->>'property_code' = :property_code" in issued_sql, (
            f"SQL missing property_code predicate: {issued_sql}"
        )
        assert "created_at > :cutoff" in issued_sql, (
            f"SQL missing created_at predicate: {issued_sql}"
        )

        # --- (b) Cutoff time-logic assertion: bound :cutoff param is approximately now - 24h ---
        mock_session_d = MagicMock()
        mock_result_d = MagicMock()
        mock_result_d.scalar.return_value = None
        mock_session_d.execute = AsyncMock(return_value=mock_result_d)

        before_call = datetime.now(timezone.utc)
        await svc._has_recent_ic_event(mock_session_d, 99, "PROP_Y", within_hours=24)
        after_call = datetime.now(timezone.utc)

        bind_params = mock_session_d.execute.call_args.args[1]
        cutoff_passed = bind_params["cutoff"]
        expected_lo = before_call - timedelta(hours=24)
        expected_hi = after_call - timedelta(hours=24)
        assert expected_lo <= cutoff_passed <= expected_hi, (
            f"cutoff {cutoff_passed!r} not within expected 24h window "
            f"[{expected_lo!r}, {expected_hi!r}]"
        )
        assert bind_params["property_code"] == "PROP_Y", (
            f"Expected property_code='PROP_Y', got: {bind_params['property_code']!r}"
        )

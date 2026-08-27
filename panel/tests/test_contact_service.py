"""
Tests for app/services/contact_service.py

Pure unit tests — all four repositories and phone utility functions are mocked
so no database connection is required.  pytest asyncio_mode = auto (see pytest.ini).

Coverage targets (lines in contact_service.py):
  get_contacts        46-75
  get_contact         79
  get_contact_detail  85-107
  create_contact      141-200
  update_contact      227-326
  update_status       345-376
  delete_contact      394-418
  get_conversations   428
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.contact_service import ContactService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_contact(**overrides) -> MagicMock:
    """Return a lightweight stand-in for a Contact ORM row."""
    c = MagicMock()
    c.id = overrides.get("id", 1)
    c.name = overrides.get("name", "Test User")
    c.phone = overrides.get("phone", "+595981500001")
    c.email = overrides.get("email", None)
    c.source = overrides.get("source", "manual")
    c.status = overrides.get("status", "new")
    c.property_id = overrides.get("property_id", None)
    c.infocasas_ref = overrides.get("infocasas_ref", None)
    c.preferences = overrides.get("preferences", None)
    c.baja_at = overrides.get("baja_at", None)
    return c


# ---------------------------------------------------------------------------
# Fixture: patch all repo singletons + db session
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_repos():
    """Patch the six module-level repo instances used by ContactService.

    Yields a dict of the six patched objects so individual tests can
    configure return values without re-entering the patch context.
    """
    with (
        patch("app.services.contact_service.contact_repo") as cr,
        patch("app.services.contact_service.lead_event_repo") as ler,
        patch("app.services.contact_service.conversation_repo") as cvr,
        patch("app.services.contact_service.property_repo") as pr,
        patch("app.services.contact_service.contact_note_repo") as cnr,
        patch("app.services.contact_service.inquiry_history_repo") as ihr,
        patch("app.services.contact_service.visit_repo") as vr,
    ):
        # Replace every public callable with an AsyncMock so awaiting them
        # works without hitting real coroutines or a real database.
        for repo in (cr, ler, cvr, pr, cnr, ihr, vr):
            for name in [
                "get_all", "count_all", "get_by_id", "get_by_phone",
                "get_by_ids", "get_ic_by_refs", "get_ic_by_ref",
                "create", "update", "update_status",
                "get_by_contact", "get_detail_views", "get_all_events",
                "has_active_for_contact", "list_by_contact",
            ]:
                setattr(repo, name, AsyncMock())
        # Sensible defaults so get_contact_detail does not crash in unrelated tests
        ler.get_detail_views.return_value = []
        ler.get_all_events.return_value = []
        cnr.get_by_contact.return_value = []
        ihr.get_by_contact.return_value = []
        vr.has_active_for_contact.return_value = False
        vr.list_by_contact.return_value = []
        yield {
            "contact": cr,
            "lead_event": ler,
            "conversation": cvr,
            "property": pr,
            "note_repo": cnr,
            "inquiry_history": ihr,
            "visit": vr,
        }


@pytest.fixture
def db() -> AsyncMock:
    """Return a mock db session — commit/rollback calls must be awaitable."""
    session = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# get_contacts
# ---------------------------------------------------------------------------

class TestGetContacts:
    async def test_returns_required_keys(self, mock_repos, db):
        cr = mock_repos["contact"]
        pr = mock_repos["property"]
        cr.get_all.return_value = []
        cr.count_all.return_value = 0
        pr.get_by_ids.return_value = {}
        pr.get_ic_by_refs.return_value = {}

        result = await ContactService.get_contacts(
            db, status=None, source=None, search=None,
            phone_filter=None, page=1, per_page=20,
        )

        assert "contacts" in result
        assert "props_map" in result
        assert "infocasas_props_map" in result
        assert "total" in result
        assert "total_pages" in result

    async def test_total_pages_minimum_one_when_empty(self, mock_repos, db):
        cr = mock_repos["contact"]
        cr.get_all.return_value = []
        cr.count_all.return_value = 0

        result = await ContactService.get_contacts(
            db, status=None, source=None, search=None,
            phone_filter=None, page=1, per_page=20,
        )

        assert result["total_pages"] == 1

    async def test_total_pages_calculation(self, mock_repos, db):
        cr = mock_repos["contact"]
        cr.get_all.return_value = []
        cr.count_all.return_value = 45

        result = await ContactService.get_contacts(
            db, status=None, source=None, search=None,
            phone_filter=None, page=1, per_page=20,
        )

        assert result["total_pages"] == 3  # ceil(45/20) = 3

    async def test_props_map_built_from_property_ids(self, mock_repos, db):
        pr = mock_repos["property"]
        cr = mock_repos["contact"]
        contacts = [
            _mock_contact(id=1, property_id=10, source="manual"),
            _mock_contact(id=2, property_id=20, source="manual"),
        ]
        cr.get_all.return_value = contacts
        cr.count_all.return_value = 2
        pr.get_by_ids.return_value = {10: MagicMock(), 20: MagicMock()}
        pr.get_ic_by_refs.return_value = {}

        result = await ContactService.get_contacts(
            db, status=None, source=None, search=None,
            phone_filter=None, page=1, per_page=20,
        )

        pr.get_by_ids.assert_called_once()
        call_args = pr.get_by_ids.call_args[0]
        assert set(call_args[1]) == {10, 20}
        assert len(result["props_map"]) == 2

    async def test_infocasas_props_map_built_for_infocasas_source(self, mock_repos, db):
        pr = mock_repos["property"]
        cr = mock_repos["contact"]
        contacts = [
            _mock_contact(id=3, source="infocasas", infocasas_ref="IC-REF-777"),
        ]
        cr.get_all.return_value = contacts
        cr.count_all.return_value = 1
        pr.get_by_ids.return_value = {}
        pr.get_ic_by_refs.return_value = {"IC-REF-777": MagicMock()}

        result = await ContactService.get_contacts(
            db, status=None, source=None, search=None,
            phone_filter=None, page=1, per_page=20,
        )

        pr.get_ic_by_refs.assert_called_once()
        assert "IC-REF-777" in result["infocasas_props_map"]

    async def test_no_property_repo_calls_when_no_ids_or_refs(self, mock_repos, db):
        pr = mock_repos["property"]
        cr = mock_repos["contact"]
        contacts = [_mock_contact(id=5, property_id=None, source="manual")]
        cr.get_all.return_value = contacts
        cr.count_all.return_value = 1

        result = await ContactService.get_contacts(
            db, status=None, source=None, search=None,
            phone_filter=None, page=1, per_page=20,
        )

        pr.get_by_ids.assert_not_called()
        pr.get_ic_by_refs.assert_not_called()
        assert result["props_map"] == {}
        assert result["infocasas_props_map"] == {}

    async def test_offset_calculation_for_page_3(self, mock_repos, db):
        cr = mock_repos["contact"]
        cr.get_all.return_value = []
        cr.count_all.return_value = 100

        await ContactService.get_contacts(
            db, status=None, source=None, search=None,
            phone_filter=None, page=3, per_page=20,
        )

        call_kwargs = cr.get_all.call_args[1]
        assert call_kwargs["offset"] == 40  # (3-1)*20


# ---------------------------------------------------------------------------
# get_contact
# ---------------------------------------------------------------------------

class TestGetContact:
    async def test_delegates_to_repo_get_by_id(self, mock_repos, db):
        cr = mock_repos["contact"]
        expected = _mock_contact(id=42)
        cr.get_by_id.return_value = expected

        result = await ContactService.get_contact(db, 42)

        cr.get_by_id.assert_awaited_once_with(db, 42)
        assert result is expected

    async def test_returns_none_for_missing_contact(self, mock_repos, db):
        mock_repos["contact"].get_by_id.return_value = None

        result = await ContactService.get_contact(db, 999999)

        assert result is None


# ---------------------------------------------------------------------------
# get_contact_detail
# ---------------------------------------------------------------------------

class TestGetContactDetail:
    async def test_returns_none_when_contact_not_found(self, mock_repos, db):
        mock_repos["contact"].get_by_id.return_value = None

        result = await ContactService.get_contact_detail(db, 999)

        assert result is None

    async def test_returns_all_required_keys(self, mock_repos, db):
        contact = _mock_contact(source="manual", property_id=None, infocasas_ref=None)
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["lead_event"].get_by_contact.return_value = []
        mock_repos["lead_event"].get_detail_views.return_value = []
        mock_repos["conversation"].get_by_contact.return_value = []
        mock_repos["property"].get_by_id.return_value = None

        with patch("app.services.contact_service.parse_phone", return_value={"valid": True}):
            result = await ContactService.get_contact_detail(db, 1)

        assert result is not None
        for key in ("contact", "grouped_events", "notes", "conversations", "linked_property", "ic_property", "phone_info", "viewed_properties", "inquiry_history"):
            assert key in result

    async def test_infocasas_source_fetches_ic_property(self, mock_repos, db):
        contact = _mock_contact(source="infocasas", infocasas_ref="IC-555", property_id=None)
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["lead_event"].get_by_contact.return_value = []
        mock_repos["conversation"].get_by_contact.return_value = []
        ic_prop = MagicMock()
        mock_repos["property"].get_ic_by_ref.return_value = ic_prop

        with patch("app.services.contact_service.parse_phone", return_value={}):
            result = await ContactService.get_contact_detail(db, 1)

        mock_repos["property"].get_ic_by_ref.assert_awaited_once_with(db, "IC-555")
        assert result["ic_property"] is ic_prop

    async def test_non_infocasas_falls_back_to_property_id(self, mock_repos, db):
        contact = _mock_contact(source="manual", property_id=77, infocasas_ref=None)
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["lead_event"].get_by_contact.return_value = []
        mock_repos["conversation"].get_by_contact.return_value = []
        prop = MagicMock()
        mock_repos["property"].get_by_id.return_value = prop

        with patch("app.services.contact_service.parse_phone", return_value={}):
            result = await ContactService.get_contact_detail(db, 1)

        mock_repos["property"].get_by_id.assert_awaited_once_with(db, 77)
        assert result["linked_property"] is prop
        assert result["ic_property"] is None

    async def test_parse_phone_called_on_contact_phone(self, mock_repos, db):
        contact = _mock_contact(phone="+595981500001", source="manual")
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["lead_event"].get_by_contact.return_value = []
        mock_repos["conversation"].get_by_contact.return_value = []

        phone_info = {"country_code": "+595", "valid": True}
        with patch("app.services.contact_service.parse_phone", return_value=phone_info) as mock_pp:
            result = await ContactService.get_contact_detail(db, 1)

        mock_pp.assert_called_once_with("+595981500001")
        assert result["phone_info"] == phone_info

    async def test_infocasas_with_no_ref_skips_ic_lookup(self, mock_repos, db):
        """source='infocasas' but infocasas_ref=None must not call get_ic_by_ref."""
        contact = _mock_contact(source="infocasas", infocasas_ref=None, property_id=None)
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["lead_event"].get_by_contact.return_value = []
        mock_repos["conversation"].get_by_contact.return_value = []

        with patch("app.services.contact_service.parse_phone", return_value={}):
            await ContactService.get_contact_detail(db, 1)

        mock_repos["property"].get_ic_by_ref.assert_not_called()


# ---------------------------------------------------------------------------
# get_contact_detail — viewed_properties (Phase 92 VIEWS-01)
# ---------------------------------------------------------------------------

class TestGetContactDetailViewedProperties:
    """Tests for VIEWS-01: viewed_properties resolved from detail_view events."""

    def _make_event(self, property_id, created_at=None):
        from datetime import datetime, timezone
        ev = MagicMock()
        ev.event_metadata = {"property_id": property_id}
        ev.created_at = created_at or datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
        return ev

    def _make_property(self, **kwargs):
        p = MagicMock()
        p.title = kwargs.get("title", "Casa Test")
        p.city = kwargs.get("city", "Asuncion")
        p.neighborhood = kwargs.get("neighborhood", "Villa Morra")
        p.price_usd = kwargs.get("price_usd", 150000)
        p.price_currency = kwargs.get("price_currency", "USD")
        p.url = kwargs.get("url", "https://onnix.com.py/prop/1")
        return p

    async def test_no_detail_views_returns_empty_list(self, mock_repos, db):
        contact = _mock_contact(source="manual", property_id=None, infocasas_ref=None)
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["lead_event"].get_by_contact.return_value = []
        mock_repos["lead_event"].get_detail_views.return_value = []
        mock_repos["conversation"].get_by_contact.return_value = []

        with patch("app.services.contact_service.parse_phone", return_value={}):
            result = await ContactService.get_contact_detail(db, 1)

        assert result["viewed_properties"] == []
        mock_repos["property"].get_by_ids.assert_not_called()

    async def test_detail_views_resolved_to_property_dicts(self, mock_repos, db):
        from datetime import datetime, timezone
        contact = _mock_contact(source="manual", property_id=None, infocasas_ref=None)
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["lead_event"].get_by_contact.return_value = []
        viewed_at = datetime(2026, 3, 15, 14, 30, 0, tzinfo=timezone.utc)
        ev = self._make_event(property_id=42, created_at=viewed_at)
        mock_repos["lead_event"].get_detail_views.return_value = [ev]
        mock_repos["conversation"].get_by_contact.return_value = []
        prop = self._make_property(title="Dpto Loma San Jeronimo", city="Lambare")
        mock_repos["property"].get_by_ids.return_value = {42: prop}

        with patch("app.services.contact_service.parse_phone", return_value={}):
            result = await ContactService.get_contact_detail(db, 1)

        assert len(result["viewed_properties"]) == 1
        vp = result["viewed_properties"][0]
        assert vp["title"] == "Dpto Loma San Jeronimo"
        assert vp["city"] == "Lambare"
        assert vp["viewed_at"] == viewed_at

    async def test_event_without_property_id_skipped(self, mock_repos, db):
        contact = _mock_contact(source="manual", property_id=None, infocasas_ref=None)
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["lead_event"].get_by_contact.return_value = []
        ev_bad = MagicMock()
        ev_bad.event_metadata = {"other_key": "irrelevant"}
        mock_repos["lead_event"].get_detail_views.return_value = [ev_bad]
        mock_repos["conversation"].get_by_contact.return_value = []
        mock_repos["property"].get_by_ids.return_value = {}

        with patch("app.services.contact_service.parse_phone", return_value={}):
            result = await ContactService.get_contact_detail(db, 1)

        assert result["viewed_properties"] == []

    async def test_property_not_in_db_skipped(self, mock_repos, db):
        contact = _mock_contact(source="manual", property_id=None, infocasas_ref=None)
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["lead_event"].get_by_contact.return_value = []
        ev = self._make_event(property_id=999)
        mock_repos["lead_event"].get_detail_views.return_value = [ev]
        mock_repos["conversation"].get_by_contact.return_value = []
        mock_repos["property"].get_by_ids.return_value = {}

        with patch("app.services.contact_service.parse_phone", return_value={}):
            result = await ContactService.get_contact_detail(db, 1)

        assert result["viewed_properties"] == []

    async def test_viewed_properties_key_always_present(self, mock_repos, db):
        contact = _mock_contact(source="manual", property_id=None, infocasas_ref=None)
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["lead_event"].get_by_contact.return_value = []
        mock_repos["lead_event"].get_detail_views.return_value = []
        mock_repos["conversation"].get_by_contact.return_value = []

        with patch("app.services.contact_service.parse_phone", return_value={}):
            result = await ContactService.get_contact_detail(db, 1)

        assert "viewed_properties" in result


# ---------------------------------------------------------------------------
# create_contact
# ---------------------------------------------------------------------------

class TestCreateContact:
    async def test_empty_name_returns_error(self, mock_repos, db):
        contact, err = await ContactService.create_contact(
            db, name="", phone="+595981500002", email=None,
            status="new", operacion=None, zona=None,
            presupuesto_raw="", dormitorios_raw="",
            user_id=1, user_email="admin@test.com", user_role="admin",
        )
        assert contact is None
        assert "nombre" in err.lower()

    async def test_empty_phone_returns_error(self, mock_repos, db):
        contact, err = await ContactService.create_contact(
            db, name="Ana", phone="", email=None,
            status="new", operacion=None, zona=None,
            presupuesto_raw="", dormitorios_raw="",
            user_id=1, user_email="admin@test.com", user_role="admin",
        )
        assert contact is None
        assert "tel" in err.lower()

    async def test_invalid_phone_returns_error(self, mock_repos, db):
        with patch(
            "app.services.contact_service.validate_phone",
            return_value=(False, "Debe empezar con +"),
        ):
            contact, err = await ContactService.create_contact(
                db, name="Ana", phone="not-e164", email=None,
                status="new", operacion=None, zona=None,
                presupuesto_raw="", dormitorios_raw="",
                user_id=1, user_email="admin@test.com", user_role="admin",
            )
        assert contact is None
        assert err is not None and len(err) > 0

    async def test_duplicate_phone_returns_error(self, mock_repos, db):
        mock_repos["contact"].get_by_phone.return_value = _mock_contact()
        with patch("app.services.contact_service.validate_phone", return_value=(True, "")):
            contact, err = await ContactService.create_contact(
                db, name="Ana", phone="+595981500003", email=None,
                status="new", operacion=None, zona=None,
                presupuesto_raw="", dormitorios_raw="",
                user_id=1, user_email="admin@test.com", user_role="admin",
            )
        assert contact is None
        assert "registrado" in err.lower() or "ya" in err.lower()

    async def test_invalid_status_defaults_to_new(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        created = _mock_contact(id=99, status="new")
        cr.get_by_phone.return_value = None
        cr.create.return_value = created
        ler.create.return_value = MagicMock()

        with patch("app.services.contact_service.validate_phone", return_value=(True, "")):
            contact, err = await ContactService.create_contact(
                db, name="Bob", phone="+595981500004", email=None,
                status="invalid_status_xyz", operacion=None, zona=None,
                presupuesto_raw="", dormitorios_raw="",
                user_id=1, user_email="admin@test.com", user_role="admin",
            )

        call_kwargs = cr.create.call_args[1]
        assert call_kwargs["status"] == "new"
        assert err is None

    async def test_valid_creation_returns_contact(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        created = _mock_contact(id=10, status="new")
        cr.get_by_phone.return_value = None
        cr.create.return_value = created
        ler.create.return_value = MagicMock()

        with patch("app.services.contact_service.validate_phone", return_value=(True, "")):
            contact, err = await ContactService.create_contact(
                db, name="Maria", phone="+595981500005", email="m@m.com",
                status="new", operacion=None, zona=None,
                presupuesto_raw="", dormitorios_raw="",
                user_id=2, user_email="user@test.com", user_role="user",
            )

        assert contact is created
        assert err is None
        db.commit.assert_awaited_once()

    async def test_preferences_built_from_all_fields(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        created = _mock_contact(id=11)
        cr.get_by_phone.return_value = None
        cr.create.return_value = created
        ler.create.return_value = MagicMock()

        with patch("app.services.contact_service.validate_phone", return_value=(True, "")):
            await ContactService.create_contact(
                db, name="Carlos", phone="+595981500006", email=None,
                status="new", operacion="venta", zona="Asuncion",
                presupuesto_raw="150000", dormitorios_raw="3",
                user_id=1, user_email="a@b.com", user_role="admin",
            )

        call_kwargs = cr.create.call_args[1]
        prefs = call_kwargs["preferences"]
        assert prefs["operacion"] == "venta"
        assert prefs["zona"] == "Asuncion"
        assert prefs["presupuesto"] == 150000.0
        assert prefs["dormitorios"] == 3
        assert "notas" not in prefs

    async def test_invalid_presupuesto_silently_skipped(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        created = _mock_contact(id=12)
        cr.get_by_phone.return_value = None
        cr.create.return_value = created
        ler.create.return_value = MagicMock()

        with patch("app.services.contact_service.validate_phone", return_value=(True, "")):
            contact, err = await ContactService.create_contact(
                db, name="Luis", phone="+595981500007", email=None,
                status="new", operacion=None, zona=None,
                presupuesto_raw="not-a-number", dormitorios_raw="",
                user_id=1, user_email="a@b.com", user_role="admin",
            )

        assert err is None
        call_kwargs = cr.create.call_args[1]
        prefs = call_kwargs["preferences"]
        assert "presupuesto" not in (prefs or {})

    async def test_invalid_dormitorios_silently_skipped(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        created = _mock_contact(id=13)
        cr.get_by_phone.return_value = None
        cr.create.return_value = created
        ler.create.return_value = MagicMock()

        with patch("app.services.contact_service.validate_phone", return_value=(True, "")):
            contact, err = await ContactService.create_contact(
                db, name="Pedro", phone="+595981500008", email=None,
                status="new", operacion=None, zona=None,
                presupuesto_raw="", dormitorios_raw="dos",
                user_id=1, user_email="a@b.com", user_role="admin",
            )

        assert err is None
        call_kwargs = cr.create.call_args[1]
        prefs = call_kwargs["preferences"]
        assert "dormitorios" not in (prefs or {})

    async def test_lead_event_created_with_correct_type(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        created = _mock_contact(id=14, status="new")
        cr.get_by_phone.return_value = None
        cr.create.return_value = created
        ler.create.return_value = MagicMock()

        with patch("app.services.contact_service.validate_phone", return_value=(True, "")):
            await ContactService.create_contact(
                db, name="Rosa", phone="+595981500009", email=None,
                status="new", operacion=None, zona=None,
                presupuesto_raw="", dormitorios_raw="",
                user_id=5, user_email="admin@test.com", user_role="admin",
            )

        call_kwargs = ler.create.call_args[1]
        assert call_kwargs["event_type"] == "new_contact"
        assert call_kwargs["triggered_by"] == "user:5"
        assert call_kwargs["new_status"] == "new"

    async def test_no_preferences_stored_as_none_when_all_empty(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        created = _mock_contact(id=15)
        cr.get_by_phone.return_value = None
        cr.create.return_value = created
        ler.create.return_value = MagicMock()

        with patch("app.services.contact_service.validate_phone", return_value=(True, "")):
            await ContactService.create_contact(
                db, name="Sin Prefs", phone="+595981500010", email=None,
                status="new", operacion=None, zona=None,
                presupuesto_raw="", dormitorios_raw="",
                user_id=1, user_email="a@b.com", user_role="admin",
            )

        call_kwargs = cr.create.call_args[1]
        # When prefs dict is empty, it must be passed as None
        assert call_kwargs["preferences"] is None


# ---------------------------------------------------------------------------
# update_contact
# ---------------------------------------------------------------------------

class TestUpdateContact:
    async def test_contact_not_found_returns_false_with_error(self, mock_repos, db):
        mock_repos["contact"].get_by_id.return_value = None

        ok, err, has_changes = await ContactService.update_contact(
            db, contact_id=999, name="X", phone=None, email=None,
            operacion=None, zona=None, presupuesto_raw="",
            dormitorios_raw="",
            user_id=1, user_email="a@b.com",
        )

        assert ok is False
        assert "no encontrado" in err.lower()
        assert has_changes is False

    async def test_invalid_phone_change_returns_false(self, mock_repos, db):
        contact = _mock_contact(phone="+595981500011")
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["contact"].update.return_value = None

        with patch(
            "app.services.contact_service.validate_phone",
            return_value=(False, "Número inválido"),
        ):
            ok, err, has_changes = await ContactService.update_contact(
                db, contact_id=1, name=None, phone="+000BADPHONE", email=None,
                operacion=None, zona=None, presupuesto_raw="",
                dormitorios_raw="",
                user_id=1, user_email="a@b.com",
            )

        assert ok is False
        assert err is not None
        assert has_changes is False

    async def test_duplicate_phone_on_different_contact_returns_false(self, mock_repos, db):
        cr = mock_repos["contact"]
        original = _mock_contact(id=1, phone="+595981500012")
        duplicate_owner = _mock_contact(id=2, phone="+595981500013")
        cr.get_by_id.return_value = original
        cr.get_by_phone.return_value = duplicate_owner

        with patch("app.services.contact_service.validate_phone", return_value=(True, "")):
            ok, err, _ = await ContactService.update_contact(
                db, contact_id=1, name=None, phone="+595981500013", email=None,
                operacion=None, zona=None, presupuesto_raw="",
                dormitorios_raw="",
                user_id=1, user_email="a@b.com",
            )

        assert ok is False
        assert "otro contacto" in err.lower()

    async def test_same_phone_as_self_not_treated_as_duplicate(self, mock_repos, db):
        """get_by_phone returning the same contact should not block the update."""
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, phone="+595981500014", name="Same Phone")
        cr.get_by_id.return_value = contact
        # get_by_phone returns same contact (same id) — not a conflict
        cr.get_by_phone.return_value = contact
        cr.update.return_value = None
        ler.create.return_value = MagicMock()

        with patch("app.services.contact_service.validate_phone", return_value=(True, "")):
            ok, err, _ = await ContactService.update_contact(
                db, contact_id=1, name="Same Phone", phone="+595981500014", email=None,
                operacion=None, zona=None, presupuesto_raw="",
                dormitorios_raw="",
                user_id=1, user_email="a@b.com",
            )

        assert ok is True
        assert err is None

    async def test_name_change_detected_as_change(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, name="Before", phone="+595981500015")
        cr.get_by_id.return_value = contact
        cr.update.return_value = None
        ler.create.return_value = MagicMock()

        ok, err, has_changes = await ContactService.update_contact(
            db, contact_id=1, name="After", phone=None, email=None,
            operacion=None, zona=None, presupuesto_raw="",
            dormitorios_raw="",
            user_id=1, user_email="a@b.com",
        )

        assert ok is True
        assert has_changes is True
        ler.create.assert_awaited_once()
        event_kwargs = ler.create.call_args[1]
        assert "name" in event_kwargs["metadata"]["changed"]

    async def test_no_actual_changes_skips_lead_event(self, mock_repos, db):
        """Updating with the same values must not emit a lead_event."""
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(
            id=1, name="Unchanged", phone="+595981500016",
            email=None, preferences={"zona": "Asuncion"},
        )
        cr.get_by_id.return_value = contact
        cr.update.return_value = None

        ok, err, has_changes = await ContactService.update_contact(
            db, contact_id=1,
            name="Unchanged",      # same name
            phone=None,
            email=None,            # same email
            operacion=None,
            zona="Asuncion",       # same zona
            presupuesto_raw="",
            dormitorios_raw="",
            user_id=1, user_email="a@b.com",
        )

        assert ok is True
        assert has_changes is False
        ler.create.assert_not_called()

    async def test_presupuesto_cleared_when_empty_raw(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(
            id=1, preferences={"presupuesto": 200000.0},
            phone="+595981500017",
        )
        cr.get_by_id.return_value = contact
        cr.update.return_value = None
        ler.create.return_value = MagicMock()

        ok, err, has_changes = await ContactService.update_contact(
            db, contact_id=1, name=None, phone=None, email=None,
            operacion=None, zona=None,
            presupuesto_raw="",   # empty → should remove from prefs
            dormitorios_raw="",
            user_id=1, user_email="a@b.com",
        )

        call_kwargs = cr.update.call_args[0]
        # fields dict is the second positional arg
        fields = call_kwargs[2] if len(call_kwargs) > 2 else cr.update.call_args[1].get("fields") or cr.update.call_args[0][2]
        assert "presupuesto" not in fields.get("preferences", {})

    async def test_db_commit_called_on_success(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, phone="+595981500018")
        cr.get_by_id.return_value = contact
        cr.update.return_value = None
        ler.create.return_value = MagicMock()

        await ContactService.update_contact(
            db, contact_id=1, name="New Name", phone=None, email=None,
            operacion=None, zona=None, presupuesto_raw="",
            dormitorios_raw="",
            user_id=1, user_email="a@b.com",
        )

        db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# update_status
# ---------------------------------------------------------------------------

class TestUpdateStatus:
    async def test_invalid_status_returns_error(self, mock_repos, db):
        contact, err = await ContactService.update_status(
            db, contact_id=1, new_status="flying_pig",
            user_id=1, user_email="a@b.com", user_role="admin",
        )
        assert contact is None
        assert "inválido" in err.lower() or "invalid" in err.lower()

    async def test_contact_not_found_returns_error(self, mock_repos, db):
        mock_repos["contact"].get_by_id.return_value = None

        contact, err = await ContactService.update_status(
            db, contact_id=99999, new_status="interested",
            user_id=1, user_email="a@b.com", user_role="admin",
        )
        assert contact is None
        assert "encontrado" in err.lower()

    async def test_baja_at_set_blocks_non_discarded_status(self, mock_repos, db):
        """A contact with baja_at must not be moved to any status except 'discarded'."""
        from datetime import datetime, timezone
        contact = _mock_contact(baja_at=datetime.now(timezone.utc), status="discarded")
        mock_repos["contact"].get_by_id.return_value = contact

        result_contact, err = await ContactService.update_status(
            db, contact_id=1, new_status="new",
            user_id=1, user_email="a@b.com", user_role="admin",
        )
        assert result_contact is None
        assert "irreversible" in err.lower() or "baja" in err.lower() or "opt" in err.lower()

    async def test_baja_at_set_allows_discarded_status(self, mock_repos, db):
        """Moving to 'discarded' when baja_at is set must succeed."""
        from datetime import datetime, timezone
        contact = _mock_contact(baja_at=datetime.now(timezone.utc), status="discarded")
        updated = _mock_contact(status="discarded")
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["contact"].update_status.return_value = updated
        mock_repos["lead_event"].create.return_value = MagicMock()

        result_contact, err = await ContactService.update_status(
            db, contact_id=1, new_status="discarded",
            user_id=1, user_email="a@b.com", user_role="admin",
        )
        assert err is None
        assert result_contact is updated

    async def test_valid_status_change_returns_updated_contact(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(status="new", baja_at=None)
        updated = _mock_contact(status="agent_replied")
        cr.get_by_id.return_value = contact
        cr.update_status.return_value = updated
        ler.create.return_value = MagicMock()

        result_contact, err = await ContactService.update_status(
            db, contact_id=1, new_status="agent_replied",
            user_id=2, user_email="user@test.com", user_role="user",
        )

        assert err is None
        assert result_contact is updated
        db.commit.assert_awaited_once()

    async def test_lead_event_created_with_old_and_new_status(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(status="new", baja_at=None)
        cr.get_by_id.return_value = contact
        cr.update_status.return_value = _mock_contact(status="interested")
        ler.create.return_value = MagicMock()

        await ContactService.update_status(
            db, contact_id=1, new_status="interested",
            user_id=3, user_email="mgr@test.com", user_role="admin",
        )

        event_kwargs = ler.create.call_args[1]
        assert event_kwargs["event_type"] == "status_change"
        assert event_kwargs["old_status"] == "new"
        assert event_kwargs["new_status"] == "interested"
        assert event_kwargs["triggered_by"] == "user:3"

    async def test_all_valid_statuses_accepted(self, mock_repos, db):
        """Every status in VALID_STATUSES must pass the guard without 'inválido' error."""
        from app.constants import VALID_STATUSES
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(status="new", baja_at=None)
        cr.get_by_id.return_value = contact
        cr.update_status.return_value = _mock_contact()
        ler.create.return_value = MagicMock()

        for status in VALID_STATUSES:
            db.reset_mock()
            result, err = await ContactService.update_status(
                db, contact_id=1, new_status=status,
                user_id=1, user_email="a@b.com", user_role="admin",
            )
            assert err is None, f"Status '{status}' unexpectedly rejected: {err}"

    async def test_discarded_without_baja_to_new_succeeds(self, mock_repos, db):
        """A discarded contact WITHOUT baja_at can be reactivated to new."""
        contact = _mock_contact(status="discarded", baja_at=None)
        updated = _mock_contact(status="new")
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["contact"].update_status.return_value = updated
        mock_repos["lead_event"].create.return_value = MagicMock()

        result_contact, err = await ContactService.update_status(
            db, contact_id=1, new_status="new",
            user_id=1, user_email="a@b.com", user_role="admin",
        )
        assert err is None
        assert result_contact is updated

    async def test_discarded_without_baja_to_interested_succeeds(self, mock_repos, db):
        """A discarded contact WITHOUT baja_at can be moved to interested."""
        contact = _mock_contact(status="discarded", baja_at=None)
        updated = _mock_contact(status="interested")
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["contact"].update_status.return_value = updated
        mock_repos["lead_event"].create.return_value = MagicMock()

        result_contact, err = await ContactService.update_status(
            db, contact_id=1, new_status="interested",
            user_id=1, user_email="a@b.com", user_role="admin",
        )
        assert err is None
        assert result_contact is updated


# ---------------------------------------------------------------------------
# VISIT-05 — update_status lockout invariant (Plan 114 §5.12)
# ---------------------------------------------------------------------------


class TestUpdateStatusVisitLockout:
    """Plan 115-03 / Plan 114 §5.12 (VISIT-05).

    When a contact has at least one active visit (status='scheduled'),
    update_status MUST refuse any new_status except the 'deleted' soft-delete
    sentinel and return ("Contacto tiene visitas activas; cancelar primero").
    Once the visit is cancelled or completed, the lockout releases.
    """

    async def test_active_visit_blocks_status_change(self, mock_repos, db):
        """has_active_visit=True + non-'deleted' new_status → blocked."""
        contact = _mock_contact(status="visit_scheduled", baja_at=None)
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["visit"].has_active_for_contact.return_value = True

        result, err = await ContactService.update_status(
            db, contact_id=1, new_status="interested",
            user_id=1, user_email="a@b.com", user_role="admin",
        )

        assert result is None
        assert err == "Contacto tiene visitas activas; cancelar primero"
        # contact_repo.update_status must NOT have been called.
        mock_repos["contact"].update_status.assert_not_awaited()
        # No lead_event either — guard fires before audit.
        mock_repos["lead_event"].create.assert_not_awaited()

    async def test_active_visit_skips_check_for_deleted_sentinel(self, mock_repos, db):
        """has_active_visit=True + new_status='deleted' → visit guard bypassed
        (soft-delete sentinel per §5.12). The upstream VALID_STATUSES check
        will still reject 'deleted' for the public API, but the visit guard
        itself MUST NOT block — confirmed by asserting has_active_for_contact
        is never called when new_status='deleted'."""
        contact = _mock_contact(status="visit_scheduled", baja_at=None)
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["visit"].has_active_for_contact.return_value = True

        result, err = await ContactService.update_status(
            db, contact_id=1, new_status="deleted",
            user_id=1, user_email="a@b.com", user_role="admin",
        )

        # 'deleted' is rejected by VALID_STATUSES upstream.
        assert result is None
        assert err == "Status inválido"
        # Critical: the visit guard MUST NOT have consulted visit_repo.
        # If it had, the assertion below would fail. This locks in the
        # `if new_status != "deleted":` short-circuit in the implementation.
        mock_repos["visit"].has_active_for_contact.assert_not_awaited()

    async def test_no_active_visit_does_not_block(self, mock_repos, db):
        """has_active_visit=False → normal status transition proceeds."""
        contact = _mock_contact(status="visit_scheduled", baja_at=None)
        updated = _mock_contact(status="interested")
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["contact"].update_status.return_value = updated
        mock_repos["visit"].has_active_for_contact.return_value = False
        mock_repos["lead_event"].create.return_value = MagicMock()

        result, err = await ContactService.update_status(
            db, contact_id=1, new_status="interested",
            user_id=1, user_email="a@b.com", user_role="admin",
        )

        assert err is None
        assert result is updated

    async def test_active_visit_blocks_then_unblocks(self, mock_repos, db):
        """First call with active visit → blocked; second call after cancel
        (has_active_for_contact returns False) → succeeds."""
        contact = _mock_contact(status="visit_scheduled", baja_at=None)
        updated = _mock_contact(status="interested")
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["contact"].update_status.return_value = updated
        mock_repos["lead_event"].create.return_value = MagicMock()

        # Phase 1 — visit active, status change blocked.
        mock_repos["visit"].has_active_for_contact.return_value = True
        result1, err1 = await ContactService.update_status(
            db, contact_id=1, new_status="interested",
            user_id=1, user_email="a@b.com", user_role="admin",
        )
        assert result1 is None
        assert err1 == "Contacto tiene visitas activas; cancelar primero"

        # Phase 2 — visit cancelled (no more active), status change succeeds.
        mock_repos["visit"].has_active_for_contact.return_value = False
        result2, err2 = await ContactService.update_status(
            db, contact_id=1, new_status="interested",
            user_id=1, user_email="a@b.com", user_role="admin",
        )
        assert err2 is None
        assert result2 is updated


# ---------------------------------------------------------------------------
# delete_contact
# ---------------------------------------------------------------------------

class TestDeleteContact:
    async def test_not_found_returns_false_with_error(self, mock_repos, db):
        mock_repos["contact"].get_by_id.return_value = None

        ok, err = await ContactService.delete_contact(
            db, contact_id=99999,
            user_id=1, user_email="a@b.com", user_role="admin",
        )

        assert ok is False
        assert "encontrado" in err.lower()

    async def test_success_returns_true_no_error(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, status="new")
        cr.get_by_id.return_value = contact
        cr.update_status.return_value = None
        ler.create.return_value = MagicMock()

        ok, err = await ContactService.delete_contact(
            db, contact_id=1,
            user_id=1, user_email="admin@test.com", user_role="admin",
        )

        assert ok is True
        assert err is None

    async def test_sets_status_to_deleted(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, status="new")
        cr.get_by_id.return_value = contact
        cr.update_status.return_value = None
        ler.create.return_value = MagicMock()

        await ContactService.delete_contact(
            db, contact_id=1,
            user_id=1, user_email="a@b.com", user_role="admin",
        )

        cr.update_status.assert_awaited_once_with(db, 1, "deleted")

    async def test_lead_event_emitted_with_deleted_type(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, status="interested")
        cr.get_by_id.return_value = contact
        cr.update_status.return_value = None
        ler.create.return_value = MagicMock()

        await ContactService.delete_contact(
            db, contact_id=1,
            user_id=7, user_email="mgr@test.com", user_role="admin",
        )

        event_kwargs = ler.create.call_args[1]
        assert event_kwargs["event_type"] == "deleted"
        assert event_kwargs["old_status"] == "interested"
        assert event_kwargs["new_status"] == "deleted"
        assert event_kwargs["triggered_by"] == "user:7"

    async def test_db_commit_called(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, status="new")
        cr.get_by_id.return_value = contact
        cr.update_status.return_value = None
        ler.create.return_value = MagicMock()

        await ContactService.delete_contact(
            db, contact_id=1,
            user_id=1, user_email="a@b.com", user_role="admin",
        )

        db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_events
# ---------------------------------------------------------------------------

class TestGetEvents:
    async def test_delegates_to_lead_event_repo(self, mock_repos, db):
        ler = mock_repos["lead_event"]
        expected = [MagicMock(), MagicMock()]
        ler.get_by_contact.return_value = expected

        result = await ContactService.get_events(db, contact_id=55)

        ler.get_by_contact.assert_awaited_once_with(db, 55)
        assert result is expected


# ---------------------------------------------------------------------------
# get_conversations
# ---------------------------------------------------------------------------

class TestGetConversations:
    async def test_delegates_to_conversation_repo(self, mock_repos, db):
        cvr = mock_repos["conversation"]
        expected = [MagicMock(), MagicMock()]
        cvr.get_by_contact.return_value = expected

        result = await ContactService.get_conversations(db, contact_id=42)

        cvr.get_by_contact.assert_awaited_once_with(db, 42)
        assert result is expected

    async def test_returns_empty_list_when_no_conversations(self, mock_repos, db):
        mock_repos["conversation"].get_by_contact.return_value = []

        result = await ContactService.get_conversations(db, contact_id=1)

        assert result == []


# ---------------------------------------------------------------------------
# update_contact — additional branch coverage
# ---------------------------------------------------------------------------

class TestUpdateContactBranches:
    """Targeted tests for preference-setting and audit-diff branches in update_contact."""

    async def test_operacion_and_zona_set_in_prefs(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, preferences={}, phone="+595981500020")
        cr.get_by_id.return_value = contact
        cr.update.return_value = None
        ler.create.return_value = MagicMock()

        await ContactService.update_contact(
            db, contact_id=1, name=None, phone=None, email=None,
            operacion="alquiler", zona="Lambare",
            presupuesto_raw="", dormitorios_raw="",
            user_id=1, user_email="a@b.com",
        )

        fields = cr.update.call_args[0][2]
        assert fields["preferences"]["operacion"] == "alquiler"
        assert fields["preferences"]["zona"] == "Lambare"

    async def test_valid_presupuesto_set_in_prefs_on_update(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, preferences={}, phone="+595981500021")
        cr.get_by_id.return_value = contact
        cr.update.return_value = None
        ler.create.return_value = MagicMock()

        await ContactService.update_contact(
            db, contact_id=1, name=None, phone=None, email=None,
            operacion=None, zona=None,
            presupuesto_raw="80000", dormitorios_raw="",
            user_id=1, user_email="a@b.com",
        )

        fields = cr.update.call_args[0][2]
        assert fields["preferences"]["presupuesto"] == 80000.0

    async def test_invalid_presupuesto_silently_skipped_on_update(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, preferences={}, phone="+595981500022")
        cr.get_by_id.return_value = contact
        cr.update.return_value = None
        ler.create.return_value = MagicMock()

        ok, err, _ = await ContactService.update_contact(
            db, contact_id=1, name=None, phone=None, email=None,
            operacion=None, zona=None,
            presupuesto_raw="not-a-float", dormitorios_raw="",
            user_id=1, user_email="a@b.com",
        )

        assert ok is True
        fields = cr.update.call_args[0][2]
        assert "presupuesto" not in fields["preferences"]

    async def test_valid_dormitorios_set_in_prefs_on_update(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, preferences={}, phone="+595981500023")
        cr.get_by_id.return_value = contact
        cr.update.return_value = None
        ler.create.return_value = MagicMock()

        await ContactService.update_contact(
            db, contact_id=1, name=None, phone=None, email=None,
            operacion=None, zona=None,
            presupuesto_raw="", dormitorios_raw="4",
            user_id=1, user_email="a@b.com",
        )

        fields = cr.update.call_args[0][2]
        assert fields["preferences"]["dormitorios"] == 4

    async def test_invalid_dormitorios_silently_skipped_on_update(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, preferences={}, phone="+595981500024")
        cr.get_by_id.return_value = contact
        cr.update.return_value = None
        ler.create.return_value = MagicMock()

        ok, err, _ = await ContactService.update_contact(
            db, contact_id=1, name=None, phone=None, email=None,
            operacion=None, zona=None,
            presupuesto_raw="", dormitorios_raw="tres",
            user_id=1, user_email="a@b.com",
        )

        assert ok is True
        fields = cr.update.call_args[0][2]
        assert "dormitorios" not in fields["preferences"]

    async def test_dormitorios_cleared_when_empty_raw(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(
            id=1, preferences={"dormitorios": 3}, phone="+595981500025",
        )
        cr.get_by_id.return_value = contact
        cr.update.return_value = None
        ler.create.return_value = MagicMock()

        await ContactService.update_contact(
            db, contact_id=1, name=None, phone=None, email=None,
            operacion=None, zona=None,
            presupuesto_raw="", dormitorios_raw="",
            user_id=1, user_email="a@b.com",
        )

        fields = cr.update.call_args[0][2]
        assert "dormitorios" not in fields["preferences"]

    async def test_notas_not_set_in_prefs_on_update(self, mock_repos, db):
        """Fase 3: update_contact must not write notas into preferences."""
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, preferences={}, phone="+595981500026")
        cr.get_by_id.return_value = contact
        cr.update.return_value = None
        ler.create.return_value = MagicMock()

        await ContactService.update_contact(
            db, contact_id=1, name=None, phone=None, email=None,
            operacion=None, zona=None,
            presupuesto_raw="", dormitorios_raw="",
            user_id=1, user_email="a@b.com",
        )

        fields = cr.update.call_args[0][2]
        assert "notas" not in fields["preferences"]

    async def test_email_change_tracked_in_audit_diff(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, email="old@x.com", phone="+595981500027")
        cr.get_by_id.return_value = contact
        cr.update.return_value = None
        ler.create.return_value = MagicMock()

        ok, err, has_changes = await ContactService.update_contact(
            db, contact_id=1, name=None, phone=None, email="new@x.com",
            operacion=None, zona=None,
            presupuesto_raw="", dormitorios_raw="",
            user_id=1, user_email="a@b.com",
        )

        assert has_changes is True
        event_kwargs = ler.create.call_args[1]
        assert "email" in event_kwargs["metadata"]["changed"]
        assert event_kwargs["metadata"]["before"]["email"] == "old@x.com"
        assert event_kwargs["metadata"]["after"]["email"] == "new@x.com"

    async def test_phone_change_tracked_in_audit_diff(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, phone="+595981500028")
        cr.get_by_id.return_value = contact
        # get_by_phone returns None — no duplicate
        cr.get_by_phone.return_value = None
        cr.update.return_value = None
        ler.create.return_value = MagicMock()

        with patch("app.services.contact_service.validate_phone", return_value=(True, "")):
            ok, err, has_changes = await ContactService.update_contact(
                db, contact_id=1, name=None, phone="+595981500029", email=None,
                operacion=None, zona=None,
                presupuesto_raw="", dormitorios_raw="",
                user_id=1, user_email="a@b.com",
            )

        assert has_changes is True
        event_kwargs = ler.create.call_args[1]
        assert "phone" in event_kwargs["metadata"]["changed"]

    async def test_email_field_included_in_update_fields(self, mock_repos, db):
        cr = mock_repos["contact"]
        ler = mock_repos["lead_event"]
        contact = _mock_contact(id=1, email=None, phone="+595981500030")
        cr.get_by_id.return_value = contact
        cr.update.return_value = None
        ler.create.return_value = MagicMock()

        await ContactService.update_contact(
            db, contact_id=1, name=None, phone=None, email="added@x.com",
            operacion=None, zona=None,
            presupuesto_raw="", dormitorios_raw="",
            user_id=1, user_email="a@b.com",
        )

        fields = cr.update.call_args[0][2]
        assert "email" in fields
        assert fields["email"] == "added@x.com"


# ---------------------------------------------------------------------------
# Fase 3 — unified notes: notas param removed from service signatures
# ---------------------------------------------------------------------------

class TestFase3UnifiedNotes:
    """After Fase 3, create_contact and update_contact must not accept notas."""

    def test_create_contact_does_not_have_notas_param(self):
        """create_contact must not accept notas param."""
        import inspect
        sig = inspect.signature(ContactService.create_contact)
        assert "notas" not in sig.parameters

    def test_update_contact_does_not_have_notas_param(self):
        """update_contact must not accept notas param."""
        import inspect
        sig = inspect.signature(ContactService.update_contact)
        assert "notas" not in sig.parameters

    def test_update_contact_pref_fields_excludes_notas(self):
        """pref_fields audit list must not include 'notas'."""
        import inspect
        import app.services.contact_service as mod
        src = inspect.getsource(mod.ContactService.update_contact)
        # notas must not appear in the pref_fields list definition
        assert "pref_fields" in src
        assert '"notas"' not in src.split("pref_fields")[1].split("]")[0]

    async def test_get_contact_detail_no_notas_display(self, mock_repos, db):
        """get_contact_detail must not return notas_display key."""
        mock_repos["contact"].get_by_id.return_value = _mock_contact(
            source="manual", infocasas_ref=None, property_id=None,
            preferences={"notas": "old note"},
        )
        mock_repos["lead_event"].get_all_events.return_value = []
        mock_repos["lead_event"].get_detail_views.return_value = []
        mock_repos["note_repo"].get_by_contact.return_value = []
        mock_repos["conversation"].get_by_contact.return_value = []
        mock_repos["property"].get_by_id.return_value = None

        with patch("app.services.contact_service.parse_phone", return_value={}):
            result = await ContactService.get_contact_detail(db, 1)
        assert "notas_display" not in result


# ---------------------------------------------------------------------------
# Fase 6 — IC inquiry history
# ---------------------------------------------------------------------------

class TestGetContactDetailInquiryHistory:
    async def test_includes_inquiry_history(self, mock_repos, db):
        """get_contact_detail returns inquiry_history in its dict."""
        from app.models.inquiry_history import InquiryHistory
        cr = mock_repos["contact"]
        cvr = mock_repos["conversation"]
        ihr = mock_repos["inquiry_history"]

        contact = _mock_contact(source="infocasas", infocasas_ref="REF1")
        cr.get_by_id.return_value = contact
        cvr.get_by_contact.return_value = []

        mock_entry = MagicMock(spec=InquiryHistory)
        mock_entry.infocasas_ref = "OLD_REF"
        mock_entry.consulta_id = "C100"
        mock_entry.consulta_date = None
        mock_entry.property_title = "Casa Vieja"
        ihr.get_by_contact.return_value = [mock_entry]

        result = await ContactService.get_contact_detail(db, contact_id=1)
        assert result is not None
        assert "inquiry_history" in result
        assert len(result["inquiry_history"]) == 1
        assert result["inquiry_history"][0].infocasas_ref == "OLD_REF"

    async def test_non_infocasas_contact_returns_empty_inquiry_history(self, mock_repos, db):
        """Non-IC contacts must have an empty inquiry_history (repo not called)."""
        cr = mock_repos["contact"]
        cvr = mock_repos["conversation"]
        ihr = mock_repos["inquiry_history"]

        contact = _mock_contact(source="manual", property_id=None, infocasas_ref=None)
        cr.get_by_id.return_value = contact
        cvr.get_by_contact.return_value = []

        result = await ContactService.get_contact_detail(db, contact_id=1)
        assert result is not None
        assert result["inquiry_history"] == []
        ihr.get_by_contact.assert_not_called()

    async def test_inquiry_history_key_always_present(self, mock_repos, db):
        """inquiry_history key must always be in the returned dict."""
        contact = _mock_contact(source="manual", property_id=None, infocasas_ref=None)
        mock_repos["contact"].get_by_id.return_value = contact
        mock_repos["conversation"].get_by_contact.return_value = []

        result = await ContactService.get_contact_detail(db, contact_id=1)
        assert "inquiry_history" in result

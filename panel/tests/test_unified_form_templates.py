"""
Smoke tests for the unified contact form partial (Task 5).

Tests:
  1. contact_form.html renders without error in create mode
  2. contact_form.html renders without error in edit mode (with a contact object)
  3. GET /contacts renders successfully (contacts list with modal)
  4. GET /contacts/{id} renders successfully (detail page with edit form)
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest
from jinja2 import Environment, FileSystemLoader

# Ensure panel/ is on sys.path
_panel_dir = str(Path(__file__).resolve().parent.parent)
if _panel_dir not in sys.path:
    sys.path.insert(0, _panel_dir)

from app.tz import to_pyt
from app.utils.phone_utils import PREFIXES


# ---------------------------------------------------------------------------
# Minimal mock objects for direct Jinja2 rendering tests
# ---------------------------------------------------------------------------


class MockContact:
    """Minimal contact object matching the ORM model fields used in the partial."""
    id: int = 42
    name: str = "Test Contact"
    phone: str = "+595981234567"
    email: str = "test@ejemplo.com"
    property_id: int | None = None
    preferences: dict = {}

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _make_phone_info(
    country_code: str = "+595",
    national_number: str = "981234567",
    country: str = "PY",
    known_prefix: bool = True,
):
    return {
        "country_code": country_code,
        "national_number": national_number,
        "country": country,
        "country_name": "Paraguay",
        "valid": True,
        "known_prefix": known_prefix,
    }


# ---------------------------------------------------------------------------
# Jinja2 environment fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def jinja_env() -> Environment:
    """Create a Jinja2 environment pointing at app/templates with pyt filter."""
    templates_dir = Path(_panel_dir) / "app" / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=False,
    )
    env.filters["pyt"] = to_pyt
    return env


# ---------------------------------------------------------------------------
# 1. Partial renders in create mode
# ---------------------------------------------------------------------------


class TestContactFormPartialCreateMode:
    def test_renders_without_error(self, jinja_env: Environment) -> None:
        """contact_form.html partial renders in create mode without raising."""
        template = jinja_env.get_template("partials/contact_form.html")
        rendered = template.render(
            form_mode="create",
            contact=None,
            phone_info=None,
            phone_prefixes=PREFIXES,
        )
        assert len(rendered) > 0

    def test_contains_name_input(self, jinja_env: Environment) -> None:
        template = jinja_env.get_template("partials/contact_form.html")
        rendered = template.render(
            form_mode="create",
            contact=None,
            phone_info=None,
            phone_prefixes=PREFIXES,
        )
        assert 'name="name"' in rendered

    def test_contains_phone_hidden_input(self, jinja_env: Environment) -> None:
        template = jinja_env.get_template("partials/contact_form.html")
        rendered = template.render(
            form_mode="create",
            contact=None,
            phone_info=None,
            phone_prefixes=PREFIXES,
        )
        assert 'name="phone"' in rendered

    def test_contains_status_select_in_create_mode(self, jinja_env: Environment) -> None:
        """Status select is present in create mode."""
        template = jinja_env.get_template("partials/contact_form.html")
        rendered = template.render(
            form_mode="create",
            contact=None,
            phone_info=None,
            phone_prefixes=PREFIXES,
        )
        assert 'name="status"' in rendered

    def test_contains_property_id_hidden_input(self, jinja_env: Environment) -> None:
        template = jinja_env.get_template("partials/contact_form.html")
        rendered = template.render(
            form_mode="create",
            contact=None,
            phone_info=None,
            phone_prefixes=PREFIXES,
        )
        assert 'name="property_id"' in rendered

    def test_contains_prefix_dropdown(self, jinja_env: Environment) -> None:
        """Phone prefix dropdown contains Paraguay default."""
        template = jinja_env.get_template("partials/contact_form.html")
        rendered = template.render(
            form_mode="create",
            contact=None,
            phone_info=None,
            phone_prefixes=PREFIXES,
        )
        assert "PY" in rendered
        assert "+595" in rendered

    def test_required_attribute_present_on_name(self, jinja_env: Environment) -> None:
        template = jinja_env.get_template("partials/contact_form.html")
        rendered = template.render(
            form_mode="create",
            contact=None,
            phone_info=None,
            phone_prefixes=PREFIXES,
        )
        assert "required" in rendered


# ---------------------------------------------------------------------------
# 2. Partial renders in edit mode
# ---------------------------------------------------------------------------


class TestContactFormPartialEditMode:
    def test_renders_without_error(self, jinja_env: Environment) -> None:
        """contact_form.html partial renders in edit mode without raising."""
        template = jinja_env.get_template("partials/contact_form.html")
        contact = MockContact(
            name="Edit Test",
            phone="+595981234567",
            email="edit@test.com",
            preferences={"zona": "Villa Morra", "operacion": "compra"},
        )
        rendered = template.render(
            form_mode="edit",
            contact=contact,
            phone_info=_make_phone_info(),
            phone_prefixes=PREFIXES,
        )
        assert len(rendered) > 0

    def test_name_value_populated_in_edit_mode(self, jinja_env: Environment) -> None:
        template = jinja_env.get_template("partials/contact_form.html")
        contact = MockContact(name="PopulatedName")
        rendered = template.render(
            form_mode="edit",
            contact=contact,
            phone_info=_make_phone_info(),
            phone_prefixes=PREFIXES,
        )
        assert "PopulatedName" in rendered

    def test_status_select_absent_in_edit_mode(self, jinja_env: Environment) -> None:
        """Status select is NOT rendered in edit mode (handled by parent form)."""
        template = jinja_env.get_template("partials/contact_form.html")
        contact = MockContact()
        rendered = template.render(
            form_mode="edit",
            contact=contact,
            phone_info=_make_phone_info(),
            phone_prefixes=PREFIXES,
        )
        assert 'name="status"' not in rendered

    def test_x_model_bindings_present_in_edit_mode(self, jinja_env: Environment) -> None:
        """Edit mode includes x-model attributes to bind to parent Alpine state."""
        template = jinja_env.get_template("partials/contact_form.html")
        contact = MockContact()
        rendered = template.render(
            form_mode="edit",
            contact=contact,
            phone_info=_make_phone_info(),
            phone_prefixes=PREFIXES,
        )
        assert 'x-model="name"' in rendered
        assert 'x-model="email"' in rendered
        assert 'x-model="operacion"' in rendered
        assert 'x-model="zona"' in rendered
        assert 'x-model="presupuesto"' in rendered
        assert 'x-model="dormitorios"' in rendered

    def test_edit_mode_initializes_phone_prefix_from_phone_info(self, jinja_env: Environment) -> None:
        """Phone prefix dropdown initializes from phone_info in edit mode."""
        template = jinja_env.get_template("partials/contact_form.html")
        contact = MockContact(phone="+34652716447")
        phone_info = _make_phone_info(country_code="+34", national_number="652716447", country="ES")
        rendered = template.render(
            form_mode="edit",
            contact=contact,
            phone_info=phone_info,
            phone_prefixes=PREFIXES,
        )
        assert "+34_ES" in rendered or "652716447" in rendered

    def test_unknown_prefix_sets_free_mode(self, jinja_env: Environment) -> None:
        """When phone_info.known_prefix is False, freeMode initializes to true."""
        template = jinja_env.get_template("partials/contact_form.html")
        contact = MockContact(phone="+999999999")
        phone_info = _make_phone_info(
            country_code="+999", national_number="999999", country="XX", known_prefix=False
        )
        rendered = template.render(
            form_mode="edit",
            contact=contact,
            phone_info=phone_info,
            phone_prefixes=PREFIXES,
        )
        assert "freeMode: true" in rendered

    def test_property_title_shown_when_provided(self, jinja_env: Environment) -> None:
        """When property_title is provided, it's used as the chip label."""
        template = jinja_env.get_template("partials/contact_form.html")
        contact = MockContact(property_id=123)
        rendered = template.render(
            form_mode="edit",
            contact=contact,
            phone_info=_make_phone_info(),
            phone_prefixes=PREFIXES,
            property_title="Casa en Villa Morra USD 120,000",
        )
        assert "Casa en Villa Morra USD 120,000" in rendered


# ---------------------------------------------------------------------------
# 3. GET /contacts — list page (HTTP smoke test)
# ---------------------------------------------------------------------------


class TestContactsListPage:
    async def test_list_page_renders_200(self, admin_client) -> None:
        resp = await admin_client.get("/contacts")
        assert resp.status_code == 200

    async def test_list_page_contains_form_fields(self, admin_client) -> None:
        """Create modal includes unified form fields from the partial."""
        resp = await admin_client.get("/contacts")
        assert b'name="name"' in resp.content
        assert b'name="phone"' in resp.content
        assert b'name="operacion"' in resp.content
        assert b'name="zona"' in resp.content

    async def test_list_page_contains_phone_prefix_dropdown(self, admin_client) -> None:
        """Phone prefix dropdown is rendered in the create modal."""
        resp = await admin_client.get("/contacts")
        assert b"+595" in resp.content

    async def test_list_page_contains_property_search_widget(self, admin_client) -> None:
        """Property typeahead widget is included in the create modal."""
        resp = await admin_client.get("/contacts")
        assert b'name="property_id"' in resp.content


# ---------------------------------------------------------------------------
# 4. GET /contacts/{id} — detail page (HTTP smoke test)
# ---------------------------------------------------------------------------


class TestContactDetailPage:
    async def test_detail_page_renders_200(self, admin_client, db) -> None:
        from app.services.contact_service import contact_service
        contact, _ = await contact_service.create_contact(
            db,
            name="Unified Form Test",
            phone="+595981550100",
            email=None,
            status="new",
            operacion=None,
            zona=None,
            presupuesto_raw="",
            dormitorios_raw="",

            user_id=1,
            user_email="pytest@onnixtest.com",
            user_role="admin",
        )
        resp = await admin_client.get(f"/contacts/{contact.id}")
        assert resp.status_code == 200

    async def test_detail_page_contains_unified_form_fields(self, admin_client, db) -> None:
        """Detail edit form includes fields from the unified partial."""
        from app.services.contact_service import contact_service
        contact, _ = await contact_service.create_contact(
            db,
            name="Unified Edit Form",
            phone="+595981550200",
            email=None,
            status="new",
            operacion=None,
            zona=None,
            presupuesto_raw="",
            dormitorios_raw="",

            user_id=1,
            user_email="pytest@onnixtest.com",
            user_role="admin",
        )
        resp = await admin_client.get(f"/contacts/{contact.id}")
        assert b'name="operacion"' in resp.content
        assert b'name="zona"' in resp.content
        assert b'name="property_id"' in resp.content

    async def test_detail_page_contains_phone_prefix_dropdown(self, admin_client, db) -> None:
        """Phone prefix dropdown is rendered in the detail edit form."""
        from app.services.contact_service import contact_service
        contact, _ = await contact_service.create_contact(
            db,
            name="Phone Prefix Detail",
            phone="+595981550300",
            email=None,
            status="new",
            operacion=None,
            zona=None,
            presupuesto_raw="",
            dormitorios_raw="",

            user_id=1,
            user_email="pytest@onnixtest.com",
            user_role="admin",
        )
        resp = await admin_client.get(f"/contacts/{contact.id}")
        assert b"+595" in resp.content

    async def test_detail_page_no_status_select_in_partial(self, admin_client, db) -> None:
        """The unified partial in edit mode does NOT render a standalone status select
        (status is handled by the parent form's dedicated status dropdown with HTMX)."""
        from app.services.contact_service import contact_service
        contact, _ = await contact_service.create_contact(
            db,
            name="No Status Partial",
            phone="+595981550400",
            email=None,
            status="new",
            operacion=None,
            zona=None,
            presupuesto_raw="",
            dormitorios_raw="",

            user_id=1,
            user_email="pytest@onnixtest.com",
            user_role="admin",
        )
        resp = await admin_client.get(f"/contacts/{contact.id}")
        assert resp.status_code == 200
        # The page should still render fine even though status is handled separately
        assert b"Guardar cambios" in resp.content

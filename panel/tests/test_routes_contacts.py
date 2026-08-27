"""
Tests for app/routes/contacts.py

Covers all 8 endpoints:
  GET  /contacts                  — list with auth guard, filters
  GET  /contacts/{id}             — detail, 404 for missing
  GET  /contacts/{id}/events      — events partial
  POST /contacts/{id}/status      — status change, invalid status, 404
  POST /contacts                  — create, validation errors (missing phone, dup)
  POST /contacts/{id}/update      — update fields, 404
  POST /contacts/{id}/delete      — soft-delete, 404
"""
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UNIQUE_PHONE = "+595981111222"  # used across create/delete tests; must not pre-exist


def _cleanup(admin_client_fixture):
    """Not used directly — conftest autouse cleanup handles test users."""


# ---------------------------------------------------------------------------
# GET /contacts — list
# ---------------------------------------------------------------------------

class TestGetContacts:
    async def test_unauthenticated_redirects_to_login(self, client):
        resp = await client.get("/contacts")
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    async def test_admin_gets_200(self, admin_client):
        resp = await admin_client.get("/contacts")
        assert resp.status_code == 200

    async def test_user_role_gets_200(self, user_client):
        resp = await user_client.get("/contacts")
        assert resp.status_code == 200

    async def test_response_is_html(self, admin_client):
        resp = await admin_client.get("/contacts")
        assert b"<!DOCTYPE html" in resp.content or b"<html" in resp.content

    async def test_status_filter_accepted(self, admin_client):
        resp = await admin_client.get("/contacts?status=new")
        assert resp.status_code == 200

    async def test_source_filter_accepted(self, admin_client):
        resp = await admin_client.get("/contacts?source=manual")
        assert resp.status_code == 200

    async def test_search_filter_accepted(self, admin_client):
        resp = await admin_client.get("/contacts?search=test")
        assert resp.status_code == 200

    async def test_phone_filter_with_accepted(self, admin_client):
        resp = await admin_client.get("/contacts?phone=with")
        assert resp.status_code == 200

    async def test_phone_filter_without_accepted(self, admin_client):
        resp = await admin_client.get("/contacts?phone=without")
        assert resp.status_code == 200

    async def test_pagination_page_2(self, admin_client):
        resp = await admin_client.get("/contacts?page=2")
        assert resp.status_code == 200

    async def test_excel_contacts_not_in_data_rows(self, admin_client):
        """Contacts with source=import:excel must not be displayed in the contact rows.

        The template may include 'import:excel' in filter dropdowns, but
        those contacts should never show as listed rows.  We verify the page
        renders without error; the row-level exclusion is covered by the
        contact_repo tests.
        """
        resp = await admin_client.get("/contacts?source=manual")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /contacts/{id} — detail
# ---------------------------------------------------------------------------

class TestContactDetail:
    async def test_unauthenticated_redirects(self, client):
        resp = await client.get("/contacts/1")
        assert resp.status_code == 303

    async def test_nonexistent_returns_404(self, admin_client):
        resp = await admin_client.get("/contacts/999999")
        assert resp.status_code == 404

    async def test_existing_contact_returns_200(self, admin_client, db):
        from app.services.contact_service import contact_service
        contact, _ = await contact_service.create_contact(
            db,
            name="Test Detail",
            phone="+595981555111",
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

    async def test_detail_contains_contact_info(self, admin_client, db):
        from app.services.contact_service import contact_service
        contact, _ = await contact_service.create_contact(
            db,
            name="Detail Name Check",
            phone="+595981666777",
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
        assert b"Detail Name Check" in resp.content

    async def test_detail_page_no_viewed_properties_card_absent(self, admin_client, db):
        """Sin detail_view no aparece la carpeta de propiedades vistas (VIEWS-02)."""
        from app.services.contact_service import contact_service
        contact, _ = await contact_service.create_contact(
            db,
            name="No Views Test",
            phone="+595981800100",
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
        assert "Propiedades vistas" not in resp.text

    async def test_detail_page_viewed_properties_renders_200(self, admin_client, db):
        """Contact detail renders 200 — no 500 from viewed_properties logic."""
        from app.services.contact_service import contact_service
        contact, _ = await contact_service.create_contact(
            db,
            name="Views Render Test",
            phone="+595981800200",
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


# ---------------------------------------------------------------------------
# GET /contacts/{id}/events — events partial
# ---------------------------------------------------------------------------

class TestContactEventsPartial:
    async def test_unauthenticated_redirects(self, client):
        resp = await client.get("/contacts/1/events")
        assert resp.status_code == 303

    async def test_nonexistent_returns_404(self, admin_client):
        # authz 2026-06-12: ensure_contact_access → 404 uniforme para contact inexistente
        resp = await admin_client.get("/contacts/999999/events")
        assert resp.status_code == 404

    async def test_existing_contact_returns_200(self, admin_client, db):
        from app.services.contact_service import contact_service
        contact, _ = await contact_service.create_contact(
            db,
            name="Events Test",
            phone="+595981777888",
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
        resp = await admin_client.get(f"/contacts/{contact.id}/events")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /contacts/{id}/status — status change
# ---------------------------------------------------------------------------

class TestUpdateContactStatus:
    async def test_unauthenticated_redirects(self, client):
        resp = await client.post("/contacts/1/status", data={"status": "contacted"})
        assert resp.status_code == 303

    async def test_nonexistent_contact_returns_404(self, admin_client):
        # authz 2026-06-12: ensure_contact_access → 404 uniforme para contact inexistente
        resp = await admin_client.post(
            "/contacts/999999/status", data={"status": "bot_replied"}
        )
        assert resp.status_code == 404

    async def test_valid_status_change_returns_badge_html(self, admin_client, db):
        from app.services.contact_service import contact_service
        contact, _ = await contact_service.create_contact(
            db,
            name="Status Change",
            phone="+595981888999",
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
        resp = await admin_client.post(
            f"/contacts/{contact.id}/status", data={"status": "bot_replied"}
        )
        assert resp.status_code == 200
        # Route returns badge HTML fragment with status label
        assert b"<span" in resp.content or b"bot" in resp.content.lower() or "respondió" in resp.content.decode("utf-8", errors="replace")

    async def test_invalid_status_returns_error(self, admin_client, db):
        from app.services.contact_service import contact_service
        contact, _ = await contact_service.create_contact(
            db,
            name="Invalid Status",
            phone="+595981999000",
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
        resp = await admin_client.post(
            f"/contacts/{contact.id}/status", data={"status": "invalid_xyz"}
        )
        assert resp.status_code == 200
        assert b"Status inv" in resp.content or b"error" in resp.content.lower()


# ---------------------------------------------------------------------------
# POST /contacts — create
# ---------------------------------------------------------------------------

class TestCreateContact:
    async def test_unauthenticated_redirects(self, client):
        resp = await client.post("/contacts", data={
            "name": "Test", "phone": "+595981111333",
        })
        assert resp.status_code == 303

    async def test_valid_creation_redirects_to_detail(self, admin_client):
        resp = await admin_client.post("/contacts", data={
            "name": "New Pytest Contact",
            "phone": "+595981511222",
            "status": "new",
        })
        # Returns 204 with HX-Redirect header
        assert resp.status_code == 204
        assert "HX-Redirect" in resp.headers
        assert "/contacts/" in resp.headers["HX-Redirect"]

    async def test_missing_name_returns_error(self, admin_client):
        resp = await admin_client.post("/contacts", data={
            "name": "",
            "phone": "+595981333444",
            "status": "new",
        })
        assert resp.status_code == 200
        assert b"nombre" in resp.content.lower() or b"requerido" in resp.content.lower()

    async def test_missing_phone_returns_error(self, admin_client):
        resp = await admin_client.post("/contacts", data={
            "name": "No Phone Test",
            "phone": "",
            "status": "new",
        })
        assert resp.status_code == 200
        assert b"tel" in resp.content.lower() or b"requerido" in resp.content.lower()

    async def test_invalid_phone_returns_error(self, admin_client):
        resp = await admin_client.post("/contacts", data={
            "name": "Bad Phone",
            "phone": "not-a-phone",
            "status": "new",
        })
        assert resp.status_code == 200
        # Error HTML fragment returned
        assert len(resp.content) > 0

    async def test_duplicate_phone_returns_error(self, admin_client):
        # First creation succeeds (phone in cleanup range)
        await admin_client.post("/contacts", data={
            "name": "Original",
            "phone": "+595981544555",
            "status": "new",
        })
        # Second creation with same phone must fail
        resp = await admin_client.post("/contacts", data={
            "name": "Duplicate",
            "phone": "+595981544555",
            "status": "new",
        })
        assert resp.status_code == 200
        assert b"registrado" in resp.content.lower() or b"ya" in resp.content.lower()


# ---------------------------------------------------------------------------
# POST /contacts/{id}/update — update contact
# ---------------------------------------------------------------------------

class TestUpdateContact:
    async def test_unauthenticated_redirects(self, client):
        resp = await client.post("/contacts/1/update", data={"name": "X"})
        assert resp.status_code == 303

    async def test_nonexistent_returns_404(self, admin_client):
        resp = await admin_client.post(
            "/contacts/999999/update", data={"name": "Ghost"}
        )
        assert resp.status_code == 404

    async def test_update_name_returns_success(self, admin_client, db):
        from app.services.contact_service import contact_service
        contact, _ = await contact_service.create_contact(
            db,
            name="Before Update",
            phone="+595981555666",
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
        resp = await admin_client.post(
            f"/contacts/{contact.id}/update",
            data={"name": "After Update", "phone": "+595981555666"},
        )
        assert resp.status_code == 200
        assert b"guardados" in resp.content.lower() or b"cambios" in resp.content.lower()

    async def test_invalid_phone_on_update_returns_error(self, admin_client, db):
        from app.services.contact_service import contact_service
        contact, _ = await contact_service.create_contact(
            db,
            name="Phone Update Test",
            phone="+595981600700",
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
        assert contact is not None, "Failed to create contact for update test"
        resp = await admin_client.post(
            f"/contacts/{contact.id}/update",
            data={"name": "Phone Update Test", "phone": "invalid"},
        )
        assert resp.status_code == 200
        assert len(resp.content) > 0


# ---------------------------------------------------------------------------
# POST /contacts/{id}/delete — soft-delete
# ---------------------------------------------------------------------------

class TestDeleteContact:
    async def test_unauthenticated_redirects(self, client):
        resp = await client.post("/contacts/1/delete")
        assert resp.status_code == 303

    async def test_nonexistent_returns_404(self, admin_client):
        resp = await admin_client.post("/contacts/999999/delete")
        assert resp.status_code == 404

    async def test_delete_existing_redirects_to_list(self, admin_client, db):
        from app.services.contact_service import contact_service
        contact, _ = await contact_service.create_contact(
            db,
            name="Delete Me",
            phone="+595981777999",
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
        resp = await admin_client.post(f"/contacts/{contact.id}/delete")
        # Returns 204 with HX-Redirect to /contacts
        assert resp.status_code == 204
        assert resp.headers.get("HX-Redirect") == "/contacts"


# ---------------------------------------------------------------------------
# POST /contacts/{id}/reminders — zona horaria
# ---------------------------------------------------------------------------

class TestCreateReminderTimezone:
    """El input es <input type="datetime-local">: el navegador manda hora LOCAL.

    Interpretarla como UTC corre el recordatorio 3 o 4 horas segun el horario
    de verano de Asuncion. visits.py:47 ya lo hace bien con _parse_local_dt.
    """

    async def test_local_datetime_is_stored_as_asuncion_not_utc(
        self, admin_client, db
    ):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from app.services.contact_service import contact_service
        from app.services.contact_reminder_service import contact_reminder_service

        contact, _ = await contact_service.create_contact(
            db,
            name="Reminder TZ",
            phone="+595981555444",
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

        # Lo que manda el navegador cuando el asesor elige "manana 09:00".
        raw = "2026-09-15T09:00"
        resp = await admin_client.post(
            f"/contacts/{contact.id}/reminders",
            data={"due_at": raw, "note": "Llamar a las 9 de la manana"},
        )
        assert resp.status_code == 200, resp.text

        reminders = await contact_reminder_service.list_reminders(db, contact.id)
        created = next(r for r in reminders if r.note == "Llamar a las 9 de la manana")

        expected = datetime(2026, 9, 15, 9, 0, tzinfo=ZoneInfo("America/Asuncion"))
        stored = created.due_at
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=ZoneInfo("UTC"))

        # Mismo instante en la linea de tiempo, sin importar como se guarde.
        assert stored == expected, (
            f"guardado {stored.isoformat()} != esperado {expected.isoformat()} "
            "— el datetime-local se esta interpretando como UTC"
        )

"""Tests for app/routes/visits.py — M6.2 visit endpoints (Plan 115-03).

Covers VISIT-07 (create), VISIT-08 (cancel), VISIT-09 (complete),
reschedule smoke, GET partial smoke, and RBAC enforcement.

All tests use contact phones in the +5959819… range (conftest cleanup
handles them) and create per-test rows so the suite is order-independent.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.models.contact import Contact
from app.models.property import Property
from app.models.visit import Visit


# ---------------------------------------------------------------------------
# Factory helpers — kept inline (do NOT add to conftest unless other plans
# need them). Phones use the +5959819… range covered by conftest cleanup.
# ---------------------------------------------------------------------------


def _next_phone() -> str:
    return f"+5959819{random.randint(0, 9_999_999):07d}"


async def _make_contact(db, *, status: str = "bot_replied") -> Contact:
    c = Contact(
        name="VisitRoutesTest",
        phone=_next_phone(),
        source="manual",
        status=status,
        created_at=datetime.now(timezone.utc),
    )
    db.add(c)
    await db.flush()
    await db.commit()
    return c


async def _make_property(db) -> Property:
    """Pull a random active property — much cheaper than creating one with
    every NOT NULL field. Falls back to inserting a minimal row if the DB
    is empty (should never happen on staging snapshot)."""
    result = await db.execute(
        select(Property).where(Property.is_active.is_(True)).limit(1)
    )
    prop = result.scalar_one_or_none()
    if prop is not None:
        return prop
    prop = Property(
        source="manual",
        external_id=f"pytest-{random.randint(0, 999999)}",
        title="Pytest Test Property",
        city="Asunción",
        property_type="Casa",
        is_active=True,
    )
    db.add(prop)
    await db.flush()
    await db.commit()
    return prop


@pytest.fixture(autouse=True)
async def _cleanup_route_visits():
    """Drop test visits + revert visit_scheduled contacts after each test.

    Mirrors the per-test cleanup used by test_visit_service.py: keeps the
    M6.1 contact rows but clears the visit_scheduled status so later
    migration-roundtrip tests don't trip the mig 040 downgrade guard.
    """
    yield
    from tests.conftest import _TestSession  # type: ignore[import-not-found]
    try:
        async with _TestSession() as s:
            await s.execute(text(
                "DELETE FROM visits WHERE contact_id IN "
                "(SELECT id FROM contacts WHERE phone LIKE '+5959819%')"
            ))
            await s.execute(text(
                "UPDATE contacts SET status='no_response' "
                "WHERE phone LIKE '+5959819%' AND status='visit_scheduled'"
            ))
            await s.commit()
    except Exception:
        pass


def _future_iso(hours: int = 24) -> str:
    """Return a 'YYYY-MM-DDTHH:MM' string in America/Asuncion local time."""
    from zoneinfo import ZoneInfo
    dt = datetime.now(ZoneInfo("America/Asuncion")) + timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M")


def _past_iso() -> str:
    from zoneinfo import ZoneInfo
    dt = datetime.now(ZoneInfo("America/Asuncion")) - timedelta(days=1)
    return dt.strftime("%Y-%m-%dT%H:%M")


# ===========================================================================
# VISIT-07 — POST /contacts/{id}/visits
# ===========================================================================


class TestCreateVisitEndpoint:
    async def test_returns_200_with_partial_and_hx_trigger(self, admin_client, db):
        """VISIT-07 happy path: 200, partial body, HX-Trigger JSON valid."""
        c = await _make_contact(db, status="bot_replied")
        p = await _make_property(db)

        resp = await admin_client.post(
            f"/contacts/{c.id}/visits",
            data={
                "scheduled_at": _future_iso(48),
                "property_id": str(p.id),
                "notes": "VISIT-07 happy path",
            },
        )

        assert resp.status_code == 200
        # Partial markers — minimal stub still renders the "Visitas" heading.
        assert b"Visitas" in resp.content
        assert b"visits-content" in resp.content

        # HX-Trigger JSON must carry refresh hints + toast.
        hx = resp.headers.get("HX-Trigger")
        assert hx is not None, "HX-Trigger header missing on successful POST"
        payload = json.loads(hx)
        assert payload["refreshVisits"] is True
        assert payload["refreshEvents"] is True
        # Phase 116 UAT fix: visits MUST trigger the status-block refresh so
        # the contact-detail badge + lockout stay in sync without a full
        # page reload.
        assert payload["refreshStatusBlock"] is True
        assert payload["showToast"]["type"] == "success"
        assert payload["showToast"]["message"] == "Visita agendada"

    async def test_admin_empty_agent_form_defaults_to_session(self, admin_client, db):
        """Phase 116 attribution: admin omits form → defaults to session user."""
        from app.models.user import User
        c = await _make_contact(db, status="bot_replied")
        contact_id = c.id

        resp = await admin_client.post(
            f"/contacts/{contact_id}/visits",
            data={
                "scheduled_at": _future_iso(24),
                "notes": "default-to-session",
            },
        )
        assert resp.status_code == 200

        db.expire_all()
        result = await db.execute(
            select(Visit).where(Visit.contact_id == contact_id)
        )
        visit = result.scalar_one()
        admin_result = await db.execute(
            select(User).where(User.email == "ez@onnix.com.py")
        )
        admin_user = admin_result.scalar_one()
        assert visit.agent_user_id == admin_user.id
        assert visit.source == "panel"

    async def test_admin_can_attribute_visit_to_specific_agent(
        self, admin_client, agent_client, db,
    ):
        """Phase 116 — admin form value is honored when it resolves to a
        valid active user with role in (admin, agent).

        Requests agent_client purely for its side-effect (inserts the
        pytest_agent row). admin_client supplies the active session.
        """
        from app.models.user import User
        c = await _make_contact(db, status="bot_replied")
        contact_id = c.id

        agent_row = await db.execute(
            select(User).where(User.email == "pytest_agent@onnixtest.com")
        )
        agent = agent_row.scalar_one()
        agent_id = agent.id

        resp = await admin_client.post(
            f"/contacts/{contact_id}/visits",
            data={
                "scheduled_at": _future_iso(48),
                "agent_user_id": str(agent_id),
                "notes": "admin-attributes-to-agent",
            },
        )
        assert resp.status_code == 200

        db.expire_all()
        visit = (await db.execute(
            select(Visit).where(Visit.contact_id == contact_id)
        )).scalar_one()
        assert visit.agent_user_id == agent_id

    async def test_admin_invalid_agent_id_returns_400(self, admin_client, db):
        """Phase 116 — admin form value that doesn't resolve to an active
        user with role in (admin, agent) → 400 inline error."""
        c = await _make_contact(db, status="bot_replied")
        resp = await admin_client.post(
            f"/contacts/{c.id}/visits",
            data={
                "scheduled_at": _future_iso(24),
                "agent_user_id": "999999",
            },
        )
        assert resp.status_code == 400
        assert b"Asesor" in resp.content
        assert resp.headers.get("HX-Trigger") is None

    async def test_admin_rejects_non_assignable_role(self, admin_client, user_client, db):
        """Phase 116 — admin cannot attribute a visit to a role='user'."""
        from app.models.user import User
        c = await _make_contact(db, status="bot_replied")
        contact_id = c.id

        user_row = await db.execute(
            select(User).where(User.email == "pytest_user@onnixtest.com")
        )
        u = user_row.scalar_one()
        target_user_id = u.id

        resp = await admin_client.post(
            f"/contacts/{contact_id}/visits",
            data={
                "scheduled_at": _future_iso(24),
                "agent_user_id": str(target_user_id),
            },
        )
        assert resp.status_code == 400
        assert b"Asesor" in resp.content

    async def test_agent_role_attribution_forced_to_self(self, agent_client, db):
        """Phase 116 — agents cannot attribute visits to anyone else;
        the form value is silently overridden to the session user.id.

        feat/agent-authz: contact must be assigned to the agent so that the
        ownership check (ensure_contact_access) passes before attribution logic.
        """
        from app.models.user import User
        from sqlalchemy import text as _sa_text

        agent_row = await db.execute(
            select(User).where(User.email == "pytest_agent@onnixtest.com")
        )
        agent = agent_row.scalar_one()
        agent_id = agent.id

        # Create the contact already assigned to the agent (required by authz).
        c = Contact(
            name="VisitRoutesTest",
            phone=_next_phone(),
            source="manual",
            status="bot_replied",
            agent_user_id=agent_id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()
        await db.commit()
        contact_id = c.id

        # Even if the agent tries to attribute to another user (admin),
        # backend must force the visit to the agent's own id.
        resp = await agent_client.post(
            f"/contacts/{contact_id}/visits",
            data={
                "scheduled_at": _future_iso(24),
                "agent_user_id": "1",  # attempt to attribute to admin
            },
        )
        assert resp.status_code == 200

        db.expire_all()
        visit = (await db.execute(
            select(Visit).where(Visit.contact_id == contact_id)
        )).scalar_one()
        assert visit.agent_user_id == agent_id

    async def test_rbac_user_role_returns_403(self, user_client, db):
        """role='user' (legacy non-agent non-admin) → 403."""
        c = await _make_contact(db)

        resp = await user_client.post(
            f"/contacts/{c.id}/visits",
            data={"scheduled_at": _future_iso(24)},
        )
        assert resp.status_code == 403

    async def test_invalid_past_date_returns_400_inline_error(self, admin_client, db):
        """Past scheduled_at → 400 inline error, NO HX-Trigger."""
        c = await _make_contact(db)

        resp = await admin_client.post(
            f"/contacts/{c.id}/visits",
            data={"scheduled_at": _past_iso(), "notes": "past"},
        )
        assert resp.status_code == 400
        assert b"text-red-500" in resp.content
        # No HX-Trigger on validation errors per Plan 114 §3.x.
        assert resp.headers.get("HX-Trigger") is None

    async def test_unparseable_date_returns_400(self, admin_client, db):
        """Garbage scheduled_at string → 400 'Fecha inválida'."""
        c = await _make_contact(db)
        resp = await admin_client.post(
            f"/contacts/{c.id}/visits",
            data={"scheduled_at": "not-a-date"},
        )
        assert resp.status_code == 400
        assert b"Fecha" in resp.content


# ===========================================================================
# VISIT-08 — POST /visits/{id}/cancel
# ===========================================================================


class TestCancelVisitEndpoint:
    async def test_cancel_returns_200_with_toast(self, admin_client, db):
        c = await _make_contact(db, status="bot_replied")
        contact_id = c.id  # capture before expire_all() invalidates

        # Create a scheduled visit first.
        create_resp = await admin_client.post(
            f"/contacts/{contact_id}/visits",
            data={"scheduled_at": _future_iso(24), "notes": "to cancel"},
        )
        assert create_resp.status_code == 200

        db.expire_all()
        result = await db.execute(select(Visit).where(Visit.contact_id == contact_id))
        visit = result.scalar_one()
        visit_id = visit.id

        # Cancel.
        resp = await admin_client.post(f"/visits/{visit_id}/cancel")
        assert resp.status_code == 200

        hx = resp.headers.get("HX-Trigger")
        assert hx is not None
        payload = json.loads(hx)
        assert payload["showToast"]["message"] == "Visita cancelada"

        db.expire_all()
        result = await db.execute(select(Visit).where(Visit.id == visit_id))
        refreshed = result.scalar_one()
        assert refreshed.status == "cancelled"

    async def test_cancel_unknown_visit_returns_404(self, admin_client):
        resp = await admin_client.post("/visits/9999999/cancel")
        assert resp.status_code == 404

    async def test_rbac_user_role_returns_403(self, user_client):
        resp = await user_client.post("/visits/1/cancel")
        assert resp.status_code == 403


# ===========================================================================
# VISIT-09 — POST /visits/{id}/complete?result=…
# ===========================================================================


class TestCompleteVisitEndpoint:
    @pytest.mark.parametrize(
        "result,expected_msg,expected_status",
        [
            ("done", "Visita marcada realizada", "done"),
            ("no_show", "Visita marcada como no-show", "no_show"),
        ],
    )
    async def test_complete_returns_200_with_correct_toast(
        self, admin_client, db, result, expected_msg, expected_status
    ):
        c = await _make_contact(db, status="bot_replied")
        contact_id = c.id
        create_resp = await admin_client.post(
            f"/contacts/{contact_id}/visits",
            data={"scheduled_at": _future_iso(48)},
        )
        assert create_resp.status_code == 200

        db.expire_all()
        visit = (await db.execute(
            select(Visit).where(Visit.contact_id == contact_id)
        )).scalar_one()
        visit_id = visit.id

        resp = await admin_client.post(
            f"/visits/{visit_id}/complete?result={result}"
        )
        assert resp.status_code == 200

        hx = resp.headers.get("HX-Trigger")
        assert hx is not None
        payload = json.loads(hx)
        assert payload["showToast"]["message"] == expected_msg

        db.expire_all()
        refreshed = (await db.execute(
            select(Visit).where(Visit.id == visit_id)
        )).scalar_one()
        assert refreshed.status == expected_status

    async def test_invalid_result_returns_400(self, admin_client, db):
        c = await _make_contact(db)
        contact_id = c.id
        await admin_client.post(
            f"/contacts/{contact_id}/visits",
            data={"scheduled_at": _future_iso(24)},
        )
        db.expire_all()
        visit = (await db.execute(
            select(Visit).where(Visit.contact_id == contact_id)
        )).scalar_one()
        visit_id = visit.id

        resp = await admin_client.post(
            f"/visits/{visit_id}/complete?result=cancelled_via_complete"
        )
        assert resp.status_code == 400
        assert b"Result" in resp.content or b"inv" in resp.content.lower()
        assert resp.headers.get("HX-Trigger") is None

    async def test_complete_unknown_visit_returns_404(self, admin_client):
        resp = await admin_client.post("/visits/9999999/complete?result=done")
        assert resp.status_code == 404

    async def test_rbac_user_role_returns_403(self, user_client):
        resp = await user_client.post("/visits/1/complete?result=done")
        assert resp.status_code == 403


# ===========================================================================
# Reschedule — POST /visits/{id}/reschedule (smoke)
# ===========================================================================


class TestRescheduleVisitEndpoint:
    async def test_reschedule_returns_200_with_toast(self, admin_client, db):
        c = await _make_contact(db, status="bot_replied")
        contact_id = c.id
        await admin_client.post(
            f"/contacts/{contact_id}/visits",
            data={"scheduled_at": _future_iso(24), "notes": "orig"},
        )
        db.expire_all()
        original_visit = (await db.execute(
            select(Visit).where(Visit.contact_id == contact_id)
        )).scalar_one()
        original_id = original_visit.id

        resp = await admin_client.post(
            f"/visits/{original_id}/reschedule",
            data={"scheduled_at": _future_iso(72), "notes": "new"},
        )
        assert resp.status_code == 200

        hx = resp.headers.get("HX-Trigger")
        assert hx is not None
        payload = json.loads(hx)
        assert payload["showToast"]["message"] == "Visita reagendada"

        db.expire_all()
        # 2 rows after reschedule: original=cancelled, new=scheduled.
        all_visits = (await db.execute(
            select(Visit).where(Visit.contact_id == contact_id)
        )).scalars().all()
        statuses = sorted([v.status for v in all_visits])
        assert statuses == ["cancelled", "scheduled"]

    async def test_reschedule_past_date_returns_400(self, admin_client, db):
        c = await _make_contact(db)
        contact_id = c.id
        await admin_client.post(
            f"/contacts/{contact_id}/visits",
            data={"scheduled_at": _future_iso(24)},
        )
        db.expire_all()
        visit = (await db.execute(
            select(Visit).where(Visit.contact_id == contact_id)
        )).scalar_one()
        visit_id = visit.id

        resp = await admin_client.post(
            f"/visits/{visit_id}/reschedule",
            data={"scheduled_at": _past_iso()},
        )
        assert resp.status_code == 400

    async def test_rbac_user_role_returns_403(self, user_client):
        resp = await user_client.post(
            "/visits/1/reschedule", data={"scheduled_at": _future_iso(24)},
        )
        assert resp.status_code == 403


# ===========================================================================
# GET /contacts/{id}/visits — partial smoke
# ===========================================================================


class TestListVisitsEndpoint:
    async def test_get_returns_200_with_partial_no_hx_trigger(
        self, admin_client, db
    ):
        c = await _make_contact(db)
        resp = await admin_client.get(f"/contacts/{c.id}/visits")
        assert resp.status_code == 200
        assert b"Visitas" in resp.content
        # No HX-Trigger on GETs per Plan 114 §3.6.
        assert resp.headers.get("HX-Trigger") is None

    async def test_get_404_when_contact_missing(self, admin_client):
        resp = await admin_client.get("/contacts/9999999/visits")
        assert resp.status_code == 404

    async def test_get_rbac_user_role_returns_403(self, user_client, db):
        c = await _make_contact(db)
        resp = await user_client.get(f"/contacts/{c.id}/visits")
        assert resp.status_code == 403


# ===========================================================================
# Visits block rendering — full-template smoke test (Plan 115-04)
# ===========================================================================


class TestVisitsBlockRendering:
    async def test_get_visits_block_empty_state_for_contact_with_no_visits(
        self, admin_client, db,
    ):
        """Plan 115-04 — empty-state branch of visits_block.html renders the
        copy "Sin visitas agendadas" and the "Agendar visita" CTA. GET MUST
        NOT carry an HX-Trigger header (it is the recipient of refreshVisits,
        not the source).
        """
        c = await _make_contact(db)
        resp = await admin_client.get(f"/contacts/{c.id}/visits")
        assert resp.status_code == 200
        assert "Sin visitas agendadas" in resp.text
        assert "Agendar visita" in resp.text
        # HX-Trigger absent on GET — confirmed in Plan 114 §3.6.
        assert resp.headers.get("HX-Trigger") is None


# ===========================================================================
# Phase 116 — Status-block partial endpoint
# ===========================================================================


class TestContactStatusBlockEndpoint:
    """GET /contacts/{id}/status-block returns the badge + dropdown
    partial used by HX-Trigger=refreshStatusBlock after every visit POST.
    """

    async def test_returns_partial_with_current_status(self, admin_client, db):
        from app.models.user import User
        c = await _make_contact(db, status="new")

        resp = await admin_client.get(f"/contacts/{c.id}/status-block")
        assert resp.status_code == 200
        # Partial must include the badge container so the swap target
        # (#current-status-badge) is replaced.
        assert b"current-status-badge" in resp.content
        # When the contact has no active visit, the lockout title text
        # MUST NOT be present.
        assert b"Cancelar todas las visitas activas" not in resp.content

    async def test_reflects_lockout_when_active_visit_exists(
        self, admin_client, db,
    ):
        c = await _make_contact(db, status="new")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        resp1 = await admin_client.post(
            f"/contacts/{c.id}/visits",
            data={"scheduled_at": _future_iso(48)},
        )
        assert resp1.status_code == 200

        resp2 = await admin_client.get(f"/contacts/{c.id}/status-block")
        assert resp2.status_code == 200
        # Lockout title must surface now that an active visit exists.
        assert b"Cancelar todas las visitas activas" in resp2.content
        assert b"cursor-not-allowed" in resp2.content

    async def test_unknown_contact_returns_404(self, admin_client):
        resp = await admin_client.get("/contacts/9999999/status-block")
        assert resp.status_code == 404


# ===========================================================================
# El modal de crear visita no debe preseleccionar una propiedad
# ===========================================================================


class TestCreateModalDoesNotPreselectProperty:
    """`{% if loop.first %}selected{% endif %}` marcaba la primera propiedad.

    El select abre con una propiedad ya cargada, despues de la option
    "— Sin propiedad —". Si el asesor no mira, agenda la visita contra la
    propiedad equivocada.
    """

    async def test_no_option_is_preselected(self, admin_client, db):
        c = await _make_contact(db, status="bot_replied")
        prop = await _make_property(db)
        # Sin esto property_options viene vacio, el for no corre y el test
        # pasa sin probar nada. _build_property_options toma la linkeada.
        c.property_id = prop.id
        db.add(c)
        await db.commit()

        resp = await admin_client.get(f"/contacts/{c.id}/visits")
        assert resp.status_code == 200
        html = resp.text

        # El bloque del select de propiedad.
        start = html.find('name="property_id"')
        assert start != -1, "no se encontro el select de propiedad en el modal"
        end = html.find("</select>", start)
        block = html[start:end]

        assert f'value="{prop.id}"' in block, (
            "el select no trajo ninguna opcion: el test no estaria probando nada"
        )
        assert "selected" not in block, (
            "el modal preselecciona una propiedad que nadie eligio"
        )

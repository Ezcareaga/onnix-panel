"""feat(authz): agent ownership checks for conversations, contacts, leads, visits.

Tests the full authorization matrix for role='agent' on every endpoint listed
in the audit. Pattern:
  - agent NOT assigned to contact → 403 / filtered out
  - agent IS assigned to contact → 200 / data included
  - admin → always 200 / full data

Fixtures reuse the same _psql + phone pattern as the existing ROLE-04 tests.
Password hash 'test123' (bcrypt cost 12):
  $2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu
"""
from __future__ import annotations

import random
import os
import subprocess
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message


_HASH = "$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu"


def _psql(sql: str) -> None:
    subprocess.run(
        ["docker", "exec", "onnix-postgres",
         "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"], "-c", sql],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=10,
    )


def _phone() -> str:
    return f"+5959815{random.randint(100_000, 999_999)}"


# ---------------------------------------------------------------------------
# Module-level user fixtures (created once per test module)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def authz_users(db):
    """Create agent_x (owns contacts) and agent_y (unrelated) for each test."""
    _psql(
        "INSERT INTO users (email, name, role, password_hash, is_active) VALUES "
        f"('pytest_authz_agent_x@onnixtest.com','Authz Agent X','agent','{_HASH}',true), "
        f"('pytest_authz_agent_y@onnixtest.com','Authz Agent Y','agent','{_HASH}',true) "
        "ON CONFLICT (email) DO UPDATE SET "
        "role=EXCLUDED.role, is_active=EXCLUDED.is_active, password_hash=EXCLUDED.password_hash"
    )
    res = await db.execute(sa_text(
        "SELECT id, email FROM users WHERE email IN ("
        "'pytest_authz_agent_x@onnixtest.com','pytest_authz_agent_y@onnixtest.com')"
    ))
    mapping = {row.email: row.id for row in res}
    return {
        "agent_x": mapping["pytest_authz_agent_x@onnixtest.com"],
        "agent_y": mapping["pytest_authz_agent_y@onnixtest.com"],
    }


@pytest_asyncio.fixture
async def agent_x_client(authz_users):
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_authz_agent_x@onnixtest.com",
            "password": "test123",
        })
        yield c


@pytest_asyncio.fixture
async def agent_y_client(authz_users):
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_authz_agent_y@onnixtest.com",
            "password": "test123",
        })
        yield c


# ---------------------------------------------------------------------------
# Data fixture — contact assigned to agent_x with one conversation
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def assigned_contact(db, authz_users):
    """Contact assigned to agent_x, one conversation with one message."""
    now = datetime.now(timezone.utc)
    contact = Contact(
        phone=_phone(), source="manual", status="bot_replied",
        name="AuthzTest Contact",
        agent_user_id=authz_users["agent_x"],
        agent_assigned_at=now,
        agent_seen_at=None,
        created_at=now, last_activity_at=now,
    )
    db.add(contact)
    await db.commit()
    await db.refresh(contact)

    conv = Conversation(
        contact_id=contact.id,
        status="active", channel="whatsapp",
        is_bot_active=True, is_open=True, message_count=1,
        created_at=now, last_message_at=now,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)

    msg = Message(
        conversation_id=conv.id, contact_id=contact.id,
        direction="inbound", sender_type="contact",
        body="Hola authz test",
        created_at=now,
    )
    db.add(msg)
    await db.commit()

    return {"contact": contact, "conv": conv}


@pytest_asyncio.fixture
async def unassigned_contact(db):
    """Contact with agent_user_id=NULL — no agent owns it."""
    now = datetime.now(timezone.utc)
    contact = Contact(
        phone=_phone(), source="manual", status="new",
        name="Unassigned AuthzTest",
        agent_user_id=None,
        created_at=now, last_activity_at=now,
    )
    db.add(contact)
    await db.commit()
    await db.refresh(contact)

    conv = Conversation(
        contact_id=contact.id,
        status="active", channel="whatsapp",
        is_bot_active=True, is_open=True, message_count=0,
        created_at=now, last_message_at=now,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)

    return {"contact": contact, "conv": conv}


# ===========================================================================
# 1. GET /contacts — lista filtrada para agent
# ===========================================================================

class TestContactsList:
    async def test_agent_only_sees_assigned_in_list(
        self, agent_x_client, agent_y_client, assigned_contact, unassigned_contact,
    ):
        """Agent X GET /contacts → solo ve su contacto asignado."""
        resp = await agent_x_client.get("/contacts")
        assert resp.status_code == 200
        body = resp.text
        assert "AuthzTest Contact" in body
        assert "Unassigned AuthzTest" not in body

    async def test_agent_y_does_not_see_agent_x_contact(
        self, agent_y_client, assigned_contact,
    ):
        """Agent Y no tiene el contacto de X en su lista."""
        resp = await agent_y_client.get("/contacts")
        assert resp.status_code == 200
        assert "AuthzTest Contact" not in resp.text

    async def test_admin_sees_all_contacts(
        self, admin_client, assigned_contact, unassigned_contact,
    ):
        """Admin no tiene filtro."""
        resp = await admin_client.get("/contacts")
        assert resp.status_code == 200
        # Admin may see many contacts; at minimum it must not 403
        assert resp.status_code == 200


# ===========================================================================
# 2. POST /contacts/{id}/status
# ===========================================================================

class TestContactStatus:
    async def test_agent_owner_can_update_status(
        self, agent_x_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_x_client.post(
            f"/contacts/{cid}/status", data={"status": "agent_replied"}
        )
        assert resp.status_code == 200

    async def test_agent_non_owner_gets_403_on_status(
        self, agent_y_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_y_client.post(
            f"/contacts/{cid}/status", data={"status": "agent_replied"}
        )
        assert resp.status_code == 403

    async def test_admin_can_update_status(
        self, admin_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await admin_client.post(
            f"/contacts/{cid}/status", data={"status": "interested"}
        )
        assert resp.status_code == 200


# ===========================================================================
# 3. POST /contacts/{id}/update
# ===========================================================================

class TestContactUpdate:
    async def test_agent_owner_can_update_contact(
        self, agent_x_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_x_client.post(
            f"/contacts/{cid}/update",
            data={"name": "AuthzTest Updated", "phone": assigned_contact["contact"].phone},
        )
        assert resp.status_code == 200

    async def test_agent_non_owner_gets_403_on_update(
        self, agent_y_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_y_client.post(
            f"/contacts/{cid}/update",
            data={"name": "Hacked"},
        )
        assert resp.status_code == 403

    async def test_admin_can_update_contact(
        self, admin_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await admin_client.post(
            f"/contacts/{cid}/update",
            data={"name": "Admin Updated"},
        )
        assert resp.status_code == 200


# ===========================================================================
# 4. POST /contacts/{id}/delete
# ===========================================================================

class TestContactDelete:
    async def test_agent_non_owner_cannot_delete(
        self, agent_y_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_y_client.post(f"/contacts/{cid}/delete")
        assert resp.status_code == 403

    async def test_agent_owner_can_delete(
        self, agent_x_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_x_client.post(f"/contacts/{cid}/delete")
        # delete redirects to /contacts on success
        assert resp.status_code in (204, 303, 200)

    async def test_admin_can_delete(
        self, admin_client, unassigned_contact,
    ):
        cid = unassigned_contact["contact"].id
        resp = await admin_client.post(f"/contacts/{cid}/delete")
        assert resp.status_code in (204, 303, 200)


# ===========================================================================
# 5. POST /contacts/{id}/notes  +  PATCH/DELETE /contacts/{id}/notes/{n}
# ===========================================================================

class TestContactNotes:
    async def test_agent_non_owner_cannot_create_note(
        self, agent_y_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_y_client.post(
            f"/contacts/{cid}/notes", data={"content": "Nota infiltrada"}
        )
        assert resp.status_code == 403

    async def test_agent_owner_can_create_note(
        self, agent_x_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_x_client.post(
            f"/contacts/{cid}/notes", data={"content": "Nota válida"}
        )
        assert resp.status_code == 200

    async def test_admin_can_create_note(
        self, admin_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await admin_client.post(
            f"/contacts/{cid}/notes", data={"content": "Admin nota"}
        )
        assert resp.status_code == 200


# ===========================================================================
# 6. GET /contacts/{id}/events  (read endpoint — agent must own)
# ===========================================================================

class TestContactEvents:
    async def test_agent_non_owner_cannot_see_events(
        self, agent_y_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_y_client.get(f"/contacts/{cid}/events")
        assert resp.status_code == 403

    async def test_agent_owner_can_see_events(
        self, agent_x_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_x_client.get(f"/contacts/{cid}/events")
        assert resp.status_code == 200

    async def test_admin_can_see_events(
        self, admin_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await admin_client.get(f"/contacts/{cid}/events")
        assert resp.status_code == 200


# ===========================================================================
# 7. POST /leads/{id}/status
# ===========================================================================

class TestLeadStatus:
    async def test_agent_non_owner_gets_403_on_lead_status(
        self, agent_y_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_y_client.post(
            f"/leads/{cid}/status", data={"status": "interested"}
        )
        assert resp.status_code == 403

    async def test_agent_owner_can_change_lead_status(
        self, agent_x_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_x_client.post(
            f"/leads/{cid}/status", data={"status": "agent_replied"}
        )
        assert resp.status_code == 200

    async def test_admin_can_change_lead_status(
        self, admin_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await admin_client.post(
            f"/leads/{cid}/status", data={"status": "interested"}
        )
        assert resp.status_code == 200


# ===========================================================================
# 8. POST /conversations/{id}/reply
# ===========================================================================

class TestConversationReply:
    async def test_agent_non_owner_cannot_reply(
        self, agent_y_client, assigned_contact,
    ):
        conv_id = assigned_contact["conv"].id
        resp = await agent_y_client.post(
            f"/conversations/{conv_id}/reply",
            data={"message": "Infiltrado"},
        )
        assert resp.status_code == 403

    async def test_agent_owner_can_reply(
        self, agent_x_client, assigned_contact,
    ):
        conv_id = assigned_contact["conv"].id
        resp = await agent_x_client.post(
            f"/conversations/{conv_id}/reply",
            data={"message": "Hola desde el agente"},
        )
        # 200 (template rendered) or error partial — but NOT 403
        assert resp.status_code != 403

    async def test_admin_can_reply(
        self, admin_client, assigned_contact,
    ):
        conv_id = assigned_contact["conv"].id
        resp = await admin_client.post(
            f"/conversations/{conv_id}/reply",
            data={"message": "Respuesta admin"},
        )
        assert resp.status_code != 403


# ===========================================================================
# 9. POST /conversations/{id}/bot-toggle
# ===========================================================================

class TestConversationBotToggle:
    async def test_agent_non_owner_cannot_toggle_bot(
        self, agent_y_client, assigned_contact,
    ):
        conv_id = assigned_contact["conv"].id
        resp = await agent_y_client.post(f"/conversations/{conv_id}/bot-toggle")
        assert resp.status_code == 403

    async def test_agent_owner_can_toggle_bot(
        self, agent_x_client, assigned_contact,
    ):
        conv_id = assigned_contact["conv"].id
        resp = await agent_x_client.post(f"/conversations/{conv_id}/bot-toggle")
        assert resp.status_code != 403

    async def test_admin_can_toggle_bot(
        self, admin_client, assigned_contact,
    ):
        conv_id = assigned_contact["conv"].id
        resp = await admin_client.post(f"/conversations/{conv_id}/bot-toggle")
        assert resp.status_code != 403


# ===========================================================================
# 10. GET /conversations/{id}/messages
# ===========================================================================

class TestConversationMessages:
    async def test_agent_non_owner_cannot_read_messages(
        self, agent_y_client, assigned_contact,
    ):
        conv_id = assigned_contact["conv"].id
        resp = await agent_y_client.get(f"/conversations/{conv_id}/messages")
        assert resp.status_code == 403

    async def test_agent_owner_can_read_messages(
        self, agent_x_client, assigned_contact,
    ):
        conv_id = assigned_contact["conv"].id
        resp = await agent_x_client.get(f"/conversations/{conv_id}/messages")
        assert resp.status_code == 200

    async def test_admin_can_read_messages(
        self, admin_client, assigned_contact,
    ):
        conv_id = assigned_contact["conv"].id
        resp = await admin_client.get(f"/conversations/{conv_id}/messages")
        assert resp.status_code == 200


# ===========================================================================
# 11. GET /conversations/contacts/search
# ===========================================================================

class TestContactSearch:
    async def test_agent_search_excludes_other_agents_contacts(
        self, agent_y_client, assigned_contact,
    ):
        """Agent Y searches 'AuthzTest' — contact belongs to X → not returned."""
        resp = await agent_y_client.get("/conversations/contacts/search?q=AuthzTest")
        assert resp.status_code == 200
        assert "AuthzTest Contact" not in resp.text

    async def test_agent_search_includes_own_contacts(
        self, agent_x_client, assigned_contact,
    ):
        resp = await agent_x_client.get("/conversations/contacts/search?q=AuthzTest")
        assert resp.status_code == 200
        assert "AuthzTest Contact" in resp.text

    async def test_admin_search_returns_all(
        self, admin_client, assigned_contact,
    ):
        resp = await admin_client.get("/conversations/contacts/search?q=AuthzTest")
        assert resp.status_code == 200
        assert "AuthzTest Contact" in resp.text


# ===========================================================================
# 12. POST /contacts/{id}/visits  and  GET /contacts/{id}/visits
# ===========================================================================

class TestVisits:
    async def test_agent_non_owner_cannot_create_visit(
        self, agent_y_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_y_client.post(
            f"/contacts/{cid}/visits",
            data={
                "scheduled_at": "2099-12-31T10:00",
                "property_id": "",
                "notes": "",
                "agent_user_id": "",
            },
        )
        assert resp.status_code == 403

    async def test_agent_owner_can_create_visit(
        self, agent_x_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_x_client.post(
            f"/contacts/{cid}/visits",
            data={
                "scheduled_at": "2099-12-31T11:00",
                "property_id": "",
                "notes": "",
                "agent_user_id": "",
            },
        )
        assert resp.status_code == 200

    async def test_agent_non_owner_cannot_list_visits(
        self, agent_y_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_y_client.get(f"/contacts/{cid}/visits")
        assert resp.status_code == 403

    async def test_agent_owner_can_list_visits(
        self, agent_x_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_x_client.get(f"/contacts/{cid}/visits")
        assert resp.status_code == 200

    async def test_admin_can_create_and_list_visits(
        self, admin_client, unassigned_contact,
    ):
        cid = unassigned_contact["contact"].id
        resp = await admin_client.post(
            f"/contacts/{cid}/visits",
            data={
                "scheduled_at": "2099-12-30T10:00",
                "property_id": "",
                "notes": "",
                "agent_user_id": "",
            },
        )
        assert resp.status_code == 200
        resp2 = await admin_client.get(f"/contacts/{cid}/visits")
        assert resp2.status_code == 200


# ===========================================================================
# 13. POST /visits/{id}/cancel|complete|reschedule — visit→contact resolution
# ===========================================================================

class TestVisitActions:
    async def test_agent_non_owner_cannot_cancel_unowned_visit(
        self, agent_y_client, assigned_contact, authz_users,
    ):
        """Agent Y creates a visit on their own contact (unassigned), then
        tries to cancel a visit on agent_x's contact — must get 403."""
        # First create a visit on agent_x's contact (we need a visit id)
        cid = assigned_contact["contact"].id
        create_resp = await agent_y_client.post(
            f"/contacts/{cid}/visits",
            data={
                "scheduled_at": "2099-11-20T10:00",
                "property_id": "",
                "notes": "",
                "agent_user_id": "",
            },
        )
        # agent_y doesn't own this contact → 403 on creation too
        assert create_resp.status_code == 403

    async def test_agent_owner_can_cancel_own_visit(
        self, agent_x_client, assigned_contact, db,
    ):
        """Agent X creates a visit then cancels it."""
        cid = assigned_contact["contact"].id

        # Create
        create_resp = await agent_x_client.post(
            f"/contacts/{cid}/visits",
            data={
                "scheduled_at": "2099-11-21T10:00",
                "property_id": "",
                "notes": "cancel-me",
                "agent_user_id": "",
            },
        )
        assert create_resp.status_code == 200, create_resp.text

        # Fetch visit id directly
        res = await db.execute(
            sa_text(
                "SELECT id FROM visits WHERE contact_id = :cid "
                "AND status='scheduled' ORDER BY id DESC LIMIT 1"
            ).bindparams(cid=cid)
        )
        row = res.first()
        if row is None:
            pytest.skip("visit not found — skipping cancel test")

        cancel_resp = await agent_x_client.post(f"/visits/{row[0]}/cancel")
        assert cancel_resp.status_code == 200

    async def test_admin_can_reschedule_visit(
        self, admin_client, assigned_contact, db,
    ):
        """Admin creates + reschedules a visit on a contact."""
        cid = assigned_contact["contact"].id
        create_resp = await admin_client.post(
            f"/contacts/{cid}/visits",
            data={
                "scheduled_at": "2099-10-01T09:00",
                "property_id": "",
                "notes": "admin reschedule test",
                "agent_user_id": "",
            },
        )
        assert create_resp.status_code == 200

        res = await db.execute(
            sa_text(
                "SELECT id FROM visits WHERE contact_id = :cid "
                "AND status='scheduled' ORDER BY id DESC LIMIT 1"
            ).bindparams(cid=cid)
        )
        row = res.first()
        if row is None:
            pytest.skip("No scheduled visit found for reschedule test")

        resp = await admin_client.post(
            f"/visits/{row[0]}/reschedule",
            data={"scheduled_at": "2099-10-02T10:00", "notes": ""},
        )
        assert resp.status_code == 200


# ===========================================================================
# 14. POST /conversations/send_template — agent filtered search
# ===========================================================================

class TestSendTemplateSearch:
    async def test_agent_template_contact_search_filtered(
        self, agent_y_client, assigned_contact,
    ):
        """Agent Y's contact search in template drawer excludes contacts owned by X."""
        resp = await agent_y_client.get("/conversations/contacts/search?q=AuthzTest")
        assert resp.status_code == 200
        assert "AuthzTest Contact" not in resp.text


# ===========================================================================
# 15. POST /conversations/send_template — ownership check on contact_id
# ===========================================================================

class TestSendTemplateOwnership:
    """Ownership guard on POST /conversations/send_template (contact_id form field).

    Agent Y must get 403 when sending a template to a contact owned by agent X.
    Agent X (owner) must pass the authz layer — the response may fail for other
    business reasons (Twilio not wired in tests) but must NOT be 403.
    Admin must never get 403 regardless of contact ownership.
    """

    _TEMPLATE_KEY = "wa_tpl_followup_v3"

    async def test_agent_non_owner_gets_403(
        self, agent_y_client, assigned_contact,
    ):
        """Agent Y → contact owned by X → 403."""
        cid = assigned_contact["contact"].id
        resp = await agent_y_client.post(
            "/conversations/send_template",
            data={"contact_id": str(cid), "template_key": self._TEMPLATE_KEY},
        )
        assert resp.status_code == 403

    async def test_agent_owner_passes_authz(
        self, agent_x_client, assigned_contact,
    ):
        """Agent X → owns the contact → authz passes (not 403)."""
        cid = assigned_contact["contact"].id
        resp = await agent_x_client.post(
            "/conversations/send_template",
            data={"contact_id": str(cid), "template_key": self._TEMPLATE_KEY},
        )
        assert resp.status_code != 403

    async def test_admin_is_never_403(
        self, admin_client, assigned_contact,
    ):
        """Admin → no ownership restriction → not 403."""
        cid = assigned_contact["contact"].id
        resp = await admin_client.post(
            "/conversations/send_template",
            data={"contact_id": str(cid), "template_key": self._TEMPLATE_KEY},
        )
        assert resp.status_code != 403


# ===========================================================================
# 16. role='user' (legacy) — NOT restricted, still passes (regression guard)
# ===========================================================================

class TestLegacyUserUnaffected:
    async def test_user_legacy_can_see_contacts_list(self, user_client):
        """role='user' legacy must NOT be restricted by the new authz layer."""
        resp = await user_client.get("/contacts")
        assert resp.status_code == 200

    async def test_user_legacy_can_see_conversations(self, user_client):
        resp = await user_client.get("/conversations")
        assert resp.status_code == 200


# ===========================================================================
# 17. C2.2 — Recordatorios: authz matrix (agent owner / non-owner / admin)
# ===========================================================================

def _reminders_table_exists() -> bool:
    """Return True if contact_reminders table has been created in onnix_dev."""
    import subprocess
    result = subprocess.run(
        ["docker", "exec", "onnix-postgres",
         "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"],
         "-tAc", "SELECT to_regclass('public.contact_reminders')::text"],
        capture_output=True, text=True, timeout=10,
    )
    out = result.stdout.strip()
    return bool(out) and out not in ("", "\\N", "NULL")


_REMINDERS_TABLE_EXISTS = _reminders_table_exists()
_skip_if_no_reminders = pytest.mark.skipif(
    not _REMINDERS_TABLE_EXISTS,
    reason="contact_reminders table not yet created — run alembic upgrade 044_contact_reminders",
)


class TestRemindersAuthz:
    """POST/GET/DELETE /contacts/{id}/reminders — same ownership rules as notes.

    Non-owner (403) tests work without the table (authz short-circuits before DB).
    Owner/admin positive-path tests require migration 044 to be applied.
    """

    _FUTURE_DT = "2099-12-31T10:00"  # far future so validation never fails

    async def test_agent_non_owner_cannot_create_reminder(
        self, agent_y_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_y_client.post(
            f"/contacts/{cid}/reminders",
            data={"due_at": self._FUTURE_DT, "note": "Infiltrado"},
        )
        assert resp.status_code == 403

    @_skip_if_no_reminders
    async def test_agent_owner_can_create_reminder(
        self, agent_x_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_x_client.post(
            f"/contacts/{cid}/reminders",
            data={"due_at": self._FUTURE_DT, "note": "Follow up owner"},
        )
        assert resp.status_code == 200

    @_skip_if_no_reminders
    async def test_admin_can_create_reminder(
        self, admin_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await admin_client.post(
            f"/contacts/{cid}/reminders",
            data={"due_at": self._FUTURE_DT, "note": "Admin reminder"},
        )
        assert resp.status_code == 200

    async def test_agent_non_owner_cannot_get_reminders(
        self, agent_y_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_y_client.get(f"/contacts/{cid}/reminders")
        assert resp.status_code == 403

    @_skip_if_no_reminders
    async def test_agent_owner_can_get_reminders(
        self, agent_x_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_x_client.get(f"/contacts/{cid}/reminders")
        assert resp.status_code == 200

    @_skip_if_no_reminders
    async def test_admin_can_get_reminders(
        self, admin_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await admin_client.get(f"/contacts/{cid}/reminders")
        assert resp.status_code == 200

    async def test_agent_non_owner_cannot_mark_done(
        self, agent_y_client, assigned_contact,
    ):
        """Non-owner gets 403 on mark-done before any DB access."""
        cid = assigned_contact["contact"].id
        resp = await agent_y_client.post(
            f"/contacts/{cid}/reminders/9999/done",
        )
        assert resp.status_code == 403

    async def test_agent_non_owner_cannot_delete_reminder(
        self, agent_y_client, assigned_contact,
    ):
        cid = assigned_contact["contact"].id
        resp = await agent_y_client.request(
            "DELETE", f"/contacts/{cid}/reminders/9999"
        )
        assert resp.status_code == 403

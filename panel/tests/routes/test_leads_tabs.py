"""Plan 111-03 (Test #12 §8) — RED tests for /leads 3-tabs admin view.

Tabs spec §6.1:
    "leads"       → status IN ('new','bot_replied') AND agent_user_id IS NULL
    "interesados" → status='interested'             AND agent_user_id IS NULL
    "asignados"   → agent_user_id IS NOT NULL  (any status — ROLE-10)

Plan 111-03 must_haves:
    - admin sees 3 tabs with badge counts in /leads
    - assigned contacts NEVER appear in 'leads' or 'interesados' (ROLE-10)

Leads-workqueue (2026-06): default tab cambiado de 'leads' a 'interesados'
(la plata caliente primero) y el tab interno 'leads' se renombra visualmente
a "Nuevos" (el param ?tab=leads NO cambia). Orden visual:
Interesados → Nuevos → Sin respuesta → Asignados.
"""
from __future__ import annotations

import random
import re
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text as sa_text

from app.models.contact import Contact


async def _purge_epoch_probe_contacts(db) -> None:
    """Delete las sondas de invocaciones ANTERIORES de estos fixtures.

    Los fixtures fijan la pertenencia a la página 1 con timestamps extremos.
    Cada invocación agrega 5+ filas; después de unos tests desbordan la página
    de 25 y los asserts se ponen flaky.

    Desde el 2026-08-24 hay DOS extremos, porque las dos pestañas ya no
    ordenan igual: «Sin respuesta» sigue ASC y usa 1970; «Nuevos» pasó a DESC
    y usa 2099. El purgado tiene que cubrir los dos — si sólo mirara el pasado,
    las sondas del futuro se acumularían y volvería exactamente la flakiness
    que este helper existe para evitar.

    Sólo se tocan filas con el prefijo de teléfono de pytest Y un created_at
    imposible para un dato real.
    """
    probe_where = (
        "phone LIKE '+5959818%' "
        "AND (created_at < '1980-01-01' OR created_at > '2090-01-01')"
    )
    for tbl in ("lead_events", "messages", "conversations", "contact_notes"):
        await db.execute(sa_text(
            f"DELETE FROM {tbl} WHERE contact_id IN "
            f"(SELECT id FROM contacts WHERE {probe_where})"
        ))
    await db.execute(sa_text(f"DELETE FROM contacts WHERE {probe_where}"))
    await db.commit()


def _psql(sql: str) -> None:
    subprocess.run(
        ["docker", "exec", "onnix-postgres",
         "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"], "-c", sql],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=10,
    )


@pytest.fixture
async def tabs_fixture(db):
    """Create one helper agent + a deterministic set of contacts spanning the 3 tabs.

    Phones use a random 6-digit suffix to avoid collisions across re-runs
    (session cleanup is best-effort; randomized order can interleave runs).
    All phones live inside the +5959818 prefix range so the session cleanup
    in conftest.py removes them automatically.

    Yields a dict with helper IDs/phones so tests can assert.
    """
    await _purge_epoch_probe_contacts(db)
    _psql(
        "INSERT INTO users (email, name, role, password_hash, is_active) "
        "VALUES ('pytest_tabs_agent@onnixtest.com', 'Tabs Agent', 'agent', "
        "'$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu', true) "
        "ON CONFLICT (email) DO UPDATE SET "
        "role='agent', is_active=true, "
        "password_hash=EXCLUDED.password_hash"
    )
    res = await db.execute(sa_text(
        "SELECT id FROM users WHERE email='pytest_tabs_agent@onnixtest.com'"
    ))
    agent_id = res.scalar()

    # 6-digit random suffix per test invocation
    base_suffix = random.randint(100_000, 999_900)
    base = "+5959818"

    def ph(i: int) -> str:
        return f"{base}{base_suffix + i}"

    contacts_spec = [
        (ph(0), "new", None),
        (ph(1), "new", None),
        (ph(2), "new", None),
        (ph(3), "bot_replied", None),
        (ph(4), "bot_replied", None),
        (ph(5), "interested", None),
        (ph(6), "interested", None),
        (ph(7), "interested", None),
        (ph(8), "new", agent_id),
        (ph(9), "new", agent_id),
        (ph(10), "interested", agent_id),
        (ph(11), "interested", agent_id),
    ]
    now = datetime.now(timezone.utc)
    # Las filas de la pestaña «Nuevos» (new/bot_replied sin agente) necesitan
    # caer arriba de todo para que los asserts de pertenencia a la página 1
    # sean deterministas con cientos de filas alrededor.
    #
    # Hasta el 2026-08-24 eso se conseguía con timestamps de la ÉPOCA, porque
    # esa pestaña ordenaba ASC. Ahora ordena DESC —Ez la quiere como bandeja
    # de entrada, ver lead_repo.get_by_tab— así que el truco se da vuelta: una
    # fecha lejana en el futuro es el espejo exacto del 1970. El resto sigue
    # con `now` (interesados/asignados nunca se movieron del DESC).
    #
    # Un last_activity_at futuro es seguro para el render: compute_waiting cae
    # en "verde" con horas negativas y format_relative_es hace max(0, ...).
    tope = datetime(2099, 1, 1, tzinfo=timezone.utc)
    for i, (phone, status, agent) in enumerate(contacts_spec):
        newest_first_tab = agent is None and status in ("new", "bot_replied")
        ts = (tope + timedelta(minutes=i)) if newest_first_tab else now
        c = Contact(
            phone=phone, source="manual", status=status,
            agent_user_id=agent,
            created_at=ts,
            last_activity_at=ts,
        )
        db.add(c)
    await db.commit()

    yield {
        "agent_id": agent_id,
        "phones": [p for p, _, _ in contacts_spec],
        "leads_count": 5,
        "interesados_count": 3,
        "asignados_count": 4,
    }


def _count_rows_in_tbody(html: bytes) -> int:
    """Best-effort count of <tr id="lead-row-..."> rows in the rendered table."""
    return len(re.findall(rb'id="lead-row-\d+"', html))


def _contains_phone(html: bytes, phone: str) -> bool:
    return phone.encode() in html


class TestAdminLeadsTabs:
    async def test_admin_sees_three_tab_links(self, admin_client, tabs_fixture):
        resp = await admin_client.get("/leads")
        assert resp.status_code == 200
        body = resp.content
        # The 3 tabs must be linked with ?tab=<key>
        assert b"tab=leads" in body
        assert b"tab=interesados" in body
        assert b"tab=asignados" in body

    async def test_default_tab_is_interesados(self, admin_client, tabs_fixture):
        """Sin ?tab= el route ahora abre 'interesados' (la plata caliente
        primero) — reemplaza el default viejo 'leads' (leads-workqueue)."""
        resp = await admin_client.get("/leads")
        assert resp.status_code == 200
        # Loose check: the rendered table contains the interesados-tab rows
        # (interested, unassigned) and NOT the leads-tab rows.
        for ph in tabs_fixture["phones"][5:8]:
            assert _contains_phone(resp.content, ph), \
                f"missing interesados phone {ph} in default tab"
        for ph in tabs_fixture["phones"][:5]:
            assert not _contains_phone(resp.content, ph), \
                f"leads-tab phone {ph} leaked into default (interesados) tab"

    async def test_invalid_tab_falls_back_to_interesados(self, admin_client, tabs_fixture):
        """?tab=bogus → mismo fallback que el default (interesados)."""
        resp = await admin_client.get("/leads?tab=bogus")
        assert resp.status_code == 200
        for ph in tabs_fixture["phones"][5:8]:
            assert _contains_phone(resp.content, ph), \
                f"missing interesados phone {ph} on invalid tab fallback"

    async def test_tab_order_and_nuevos_rename(self, admin_client, tabs_fixture):
        """Orden visual: Interesados → Nuevos → Sin respuesta → Asignados.
        El tab interno 'leads' se renombra a "Nuevos" (param NO cambia)."""
        resp = await admin_client.get("/leads")
        assert resp.status_code == 200
        body = resp.content.decode("utf-8", errors="ignore")
        pos = {
            "interesados": body.find('href="/leads?tab=interesados'),
            "leads": body.find('href="/leads?tab=leads'),
            "sin_respuesta": body.find('href="/leads?tab=sin_respuesta'),
            "asignados": body.find('href="/leads?tab=asignados'),
        }
        for tab, p in pos.items():
            assert p != -1, f"tab anchor {tab} missing"
        assert pos["interesados"] < pos["leads"] < pos["sin_respuesta"] < pos["asignados"], (
            f"tab visual order wrong: {pos}"
        )
        # The tab=leads anchor must read "Nuevos", not "Leads".
        leads_anchor = body[pos["leads"]:].split("</a>", 1)[0]
        assert "Nuevos" in leads_anchor, "tab=leads anchor must be labeled 'Nuevos'"
        assert ">Leads<" not in leads_anchor

    async def test_tab_leads_query(self, admin_client, tabs_fixture):
        resp = await admin_client.get("/leads?tab=leads")
        assert resp.status_code == 200
        body = resp.content
        # 5 phones should be present (new/bot_replied AND agent_user_id IS NULL)
        for ph in tabs_fixture["phones"][:5]:
            assert _contains_phone(body, ph), f"missing leads phone {ph}"
        # ROLE-10: assigned-but-status=new contacts (idx 8,9) must NOT appear
        for ph in tabs_fixture["phones"][8:10]:
            assert not _contains_phone(body, ph), f"assigned phone leaked into leads: {ph}"

    async def test_tab_interesados_query(self, admin_client, tabs_fixture):
        resp = await admin_client.get("/leads?tab=interesados")
        assert resp.status_code == 200
        body = resp.content
        # 3 phones (interested, unassigned)
        for ph in tabs_fixture["phones"][5:8]:
            assert _contains_phone(body, ph), f"missing interesados phone {ph}"
        # ROLE-10: assigned-interested (idx 10,11) must NOT appear
        for ph in tabs_fixture["phones"][10:12]:
            assert not _contains_phone(body, ph), \
                f"assigned interested leaked into interesados: {ph}"

    async def test_tab_asignados_query(self, admin_client, tabs_fixture):
        resp = await admin_client.get("/leads?tab=asignados")
        assert resp.status_code == 200
        body = resp.content
        # 4 phones (any status, agent_user_id IS NOT NULL)
        for ph in tabs_fixture["phones"][8:12]:
            assert _contains_phone(body, ph), f"missing asignados phone {ph}"
        # Unassigned must NOT appear
        for ph in tabs_fixture["phones"][:8]:
            assert not _contains_phone(body, ph), \
                f"unassigned phone leaked into asignados: {ph}"

    async def test_badges_show_counts(self, admin_client, tabs_fixture):
        """Admin tab badges show the count per tab.

        Spec §6.1: each tab anchor renders a <span class="badge">N</span>.
        We don't assume exact CSS markup — just that the integer counts
        for THIS test fixture appear adjacent to the tab labels.
        """
        resp = await admin_client.get("/leads")
        body = resp.content.decode("utf-8", errors="ignore")
        # Fixture inserts 12 rows; real DB may already contain other contacts.
        # We assert at least the fixture cardinality is reachable, i.e. the
        # counts shown are >= our fixture cardinalities for each tab.
        # Regex captures any digits inside a badge-style span near each tab.
        def _badge_after_label(label: str) -> int | None:
            # Match label then up to ~300 chars then digits inside a span
            m = re.search(
                rf'{label}[\s\S]{{0,400}}?<span[^>]*>\s*(\d+)\s*</span>',
                body,
            )
            return int(m.group(1)) if m else None

        # tab=leads renombrado visualmente a "Nuevos" (leads-workqueue)
        leads_badge = _badge_after_label("Nuevos")
        interesados_badge = _badge_after_label("Interesados")
        asignados_badge = _badge_after_label("Asignados")

        assert leads_badge is not None, "Leads badge count missing"
        assert interesados_badge is not None, "Interesados badge count missing"
        assert asignados_badge is not None, "Asignados badge count missing"

        assert leads_badge >= tabs_fixture["leads_count"]
        assert interesados_badge >= tabs_fixture["interesados_count"]
        assert asignados_badge >= tabs_fixture["asignados_count"]


class TestSinRespuestaTab:
    """Plan 112-03 — 4th tab 'sin_respuesta' for no_response cohort."""

    @pytest.fixture
    async def sr_fixture(self, db):
        """Create one no_response contact (unassigned) + one new contact (unassigned).

        The no_response contact must appear in sin_respuesta but NOT in leads.
        The new contact must appear in leads but NOT in sin_respuesta.
        """
        await _purge_epoch_probe_contacts(db)
        base_suffix = random.randint(100_000, 999_900)
        base = "+5959818"

        ph_sr = f"{base}{base_suffix + 0}"   # no_response → sin_respuesta only
        ph_new = f"{base}{base_suffix + 1}"  # new         → leads only

        # Cada sonda va al extremo hacia el que ordena SU pestaña: sin_respuesta
        # sigue ASC (1970 la deja arriba), «Nuevos» pasó a DESC (2099).
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        tope = datetime(2099, 1, 1, tzinfo=timezone.utc)
        for i, (phone, status) in enumerate([(ph_sr, "no_response"), (ph_new, "new")]):
            ts = (epoch if status == "no_response" else tope) + timedelta(minutes=i)
            db.add(Contact(
                phone=phone, source="manual", status=status,
                agent_user_id=None,
                created_at=ts,
                last_activity_at=ts,
            ))
        await db.commit()

        yield {"ph_sr": ph_sr, "ph_new": ph_new}

    async def test_sin_respuesta_tab_returns_200(self, admin_client, sr_fixture):
        resp = await admin_client.get("/leads?tab=sin_respuesta")
        assert resp.status_code == 200

    async def test_no_response_contact_visible_in_sin_respuesta(
        self, admin_client, sr_fixture
    ):
        resp = await admin_client.get("/leads?tab=sin_respuesta")
        assert resp.status_code == 200
        assert _contains_phone(resp.content, sr_fixture["ph_sr"]), (
            f"no_response contact {sr_fixture['ph_sr']} not found in sin_respuesta tab"
        )

    async def test_no_response_contact_hidden_from_leads_tab(
        self, admin_client, sr_fixture
    ):
        """Regression: the original bug — no_response was invisible everywhere."""
        resp = await admin_client.get("/leads?tab=leads")
        assert resp.status_code == 200
        assert not _contains_phone(resp.content, sr_fixture["ph_sr"]), (
            f"no_response contact {sr_fixture['ph_sr']} leaked into leads tab"
        )

    async def test_new_contact_not_in_sin_respuesta(self, admin_client, sr_fixture):
        resp = await admin_client.get("/leads?tab=sin_respuesta")
        assert resp.status_code == 200
        assert not _contains_phone(resp.content, sr_fixture["ph_new"]), (
            f"new contact {sr_fixture['ph_new']} leaked into sin_respuesta tab"
        )

    async def test_count_leads_per_tab_includes_sin_respuesta(
        self, db
    ):
        from app.services.lead_service import lead_service
        counts = await lead_service.count_leads_per_tab(db)
        assert "sin_respuesta" in counts, "count_leads_per_tab missing 'sin_respuesta' key"
        assert isinstance(counts["sin_respuesta"], int)

    async def test_admin_sees_four_tab_links(self, admin_client, sr_fixture):
        resp = await admin_client.get("/leads")
        assert resp.status_code == 200
        body = resp.content
        assert b"tab=sin_respuesta" in body, "sin_respuesta tab link missing from /leads"

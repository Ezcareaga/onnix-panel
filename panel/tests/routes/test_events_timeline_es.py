"""Leads-workqueue limpieza (b) — timeline del perfil de contacto 100% en español.

Los event types nuevos del bot (bot_gate, opt_out, followup_sender,
mode_switch, zero_results_*) no tenían entrada en el mapping EL de
partials/events_timeline.html y caían al fallback
``event_type.replace('_', ' ')`` → el perfil mostraba labels en inglés
("client responded to agent", "followup sent", ...).

Seed real en onnix_dev (phone +5959818% → conftest session cleanup) y
render vía GET /contacts/{id}/events (mismo partial que el perfil).
"""
import random
from datetime import datetime, timezone

import pytest

from app.models.contact import Contact
from app.models.lead_event import LeadEvent

# (event_type, label español esperado, fallback inglés prohibido)
EVENT_LABELS_ES = [
    ("client_responded_to_agent", "Cliente respondió al asesor", "client responded to agent"),
    ("client_declined_now", "Cliente pidió no recibir opciones por ahora", "client declined now"),
    ("followup_sent", "Seguimiento automático enviado", "followup sent"),
    ("mode_switch", "Bot cambió a modo búsqueda", "mode switch"),
    ("zero_results_offered", "Bot ofreció alternativas (búsqueda sin resultados)", "zero results offered"),
    ("zero_results_accepted", "Cliente aceptó ver alternativas", "zero results accepted"),
    ("zero_results_abandoned", "Búsqueda sin resultados abandonada", "zero results abandoned"),
]


@pytest.fixture
async def contact_with_events(db):
    phone = f"+5959818{random.randint(100_000, 999_000)}"
    contact = Contact(
        phone=phone, name="Timeline ES Probe", source="whatsapp", status="new",
        created_at=datetime.now(timezone.utc),
        last_activity_at=datetime.now(timezone.utc),
    )
    db.add(contact)
    await db.flush()
    for event_type, _, _ in EVENT_LABELS_ES:
        db.add(LeadEvent(
            contact_id=contact.id,
            event_type=event_type,
            triggered_by="bot",
            event_metadata={},
        ))
    await db.commit()
    await db.refresh(contact)
    return contact


class TestTimelineEventLabelsEs:
    async def test_all_bot_event_types_render_in_spanish(
        self, admin_client, contact_with_events,
    ):
        resp = await admin_client.get(f"/contacts/{contact_with_events.id}/events")
        assert resp.status_code == 200
        html = resp.text
        for event_type, label_es, fallback_en in EVENT_LABELS_ES:
            assert label_es in html, (
                f"{event_type}: label español {label_es!r} ausente en el timeline"
            )
            assert fallback_en not in html, (
                f"{event_type}: fallback inglés {fallback_en!r} sigue renderizando"
            )

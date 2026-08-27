"""Tests for app/services/visit_service.py — M6.2 visits (Plan 115-02).

Covers:
  - VISIT-02: create_visit transitions contact.status='visit_scheduled'
    + emits lead_event('visit_status_change').
  - VISIT-03: cancel last active visit keeps contact in 'visit_scheduled'
    (no auto-transition, no lead_event).
  - VISIT-04: reschedule is atomic (cancel old + insert new in 1 tx).
  - VISIT-10: future-date validation on create + reschedule.
  - VISIT-11 (optional): multi-active visits keep flag until all terminal.

All tests run against onnix_dev. Test contacts use phone prefix
'+5959818…' / '+5959819…' so the session cleanup fixture in conftest.py
removes them automatically.
"""
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.contact import Contact
from app.models.lead_event import LeadEvent
from app.models.visit import Visit
from app.services.visit_service import visit_service
from app.services.contact_service import contact_service
from app.repositories.visit_repo import visit_repo


# ---------------------------------------------------------------------------
# Local factory helpers — kept inline per Plan 115-02 instructions (do NOT
# add to conftest unless other plans need them).
# ---------------------------------------------------------------------------


def _next_phone() -> str:
    # +5959819XXXXXXX — within the conftest test cleanup range
    # (+5959815…/16…/17…/18…/19…). Random 7-digit suffix avoids collisions
    # both across tests in this module and across reruns that leave stale rows.
    return f"+5959819{random.randint(0, 9_999_999):07d}"


async def _make_contact(db, *, status: str = "bot_replied") -> Contact:
    c = Contact(
        name="VisitTest",
        phone=_next_phone(),
        source="manual",
        status=status,
        created_at=datetime.now(timezone.utc),
    )
    db.add(c)
    await db.flush()
    return c


@pytest.fixture(autouse=True)
async def _cleanup_visit_test_contacts():
    """Per-test cleanup: drop ONLY rows that contradict the mig 040 downgrade
    guard — visits + the visit_scheduled status itself.

    Background: mig 040's downgrade aborts when any contact has
    status='visit_scheduled'. Visit-service tests transition contacts to
    that status mid-suite; if a later migration roundtrip test runs
    (test_039_upgrade_downgrade_roundtrip), its downgrade will fail unless
    we clear the offending rows first.

    Earlier iterations of this fixture deleted contacts wholesale in the
    +5959819% range, which clobbered data created by other suites that
    share the +5959815-19 range (per conftest.TEST_PHONE_PREFIX_SQL). We
    now narrow to: (a) delete visits rows, (b) revert visit_scheduled
    contacts to 'no_response'. Contacts and unrelated tables stay intact;
    the session-scoped conftest cleanup remains the authoritative purge.
    """
    yield
    from sqlalchemy import text
    from tests.conftest import _TestSession  # type: ignore[import-not-found]
    try:
        async with _TestSession() as s:
            # 1. Drop any visit rows this module created — visits table is
            #    owned only by M6.2 tests so this is a safe blanket delete
            #    against contacts in our phone range.
            await s.execute(text(
                "DELETE FROM visits WHERE contact_id IN "
                "(SELECT id FROM contacts WHERE phone LIKE '+5959819%')"
            ))
            # 2. Revert any contact this test flipped to 'visit_scheduled'
            #    so the mig 040 downgrade guard doesn't fire later in the
            #    session. Status reverts to 'no_response' (safe default that
            #    survives the M6.1 contacts_status_check). Do NOT delete the
            #    contact row — other suites may be using it.
            await s.execute(text(
                "UPDATE contacts SET status = 'no_response' "
                "WHERE phone LIKE '+5959819%' AND status = 'visit_scheduled'"
            ))
            await s.commit()
    except Exception:
        pass  # Best-effort — session-scoped conftest cleanup is the safety net.


# ---------------------------------------------------------------------------
# VISIT-02
# ---------------------------------------------------------------------------

class TestVisitServiceSyncStatus:
    async def test_create_visit_transitions_contact_to_visit_scheduled(self, db):
        """VISIT-02: create_visit on a contact whose status != 'visit_scheduled'
        flips the status to 'visit_scheduled' AND emits exactly one
        lead_event(event_type='visit_status_change').
        """
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)

        visit, error = await visit_service.create_visit(
            db,
            contact_id=c.id,
            scheduled_at=future,
            agent_user_id=None,
        )

        assert error is None
        assert visit is not None
        assert visit.id is not None
        assert visit.status == "scheduled"

        # Contact transitioned.
        await db.refresh(c)
        assert c.status == "visit_scheduled"

        # Exactly 1 lead_event(visit_status_change).
        evt_q = await db.execute(
            select(LeadEvent).where(
                LeadEvent.contact_id == c.id,
                LeadEvent.event_type == "visit_status_change",
            )
        )
        events = list(evt_q.scalars().all())
        assert len(events) == 1
        evt = events[0]
        assert evt.old_status == "bot_replied"
        assert evt.new_status == "visit_scheduled"
        assert evt.triggered_by == f"visit_service:{visit.id}"

    # ------------------------------------------------------------------
    # VISIT-03
    # ------------------------------------------------------------------
    async def test_cancel_last_visit_keeps_contact_in_visit_scheduled(self, db):
        """VISIT-03: cancelling the ONLY active visit leaves the contact in
        'visit_scheduled' (no auto-transition per VISIT-05). NO new
        lead_event(visit_status_change) is emitted by the cancel call.
        """
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)

        # Set up via create_visit (one lead_event will be emitted here).
        visit, err = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future, agent_user_id=None,
        )
        assert err is None and visit is not None

        # Count events emitted by create — should be exactly 1.
        evt_q = await db.execute(
            select(LeadEvent).where(
                LeadEvent.contact_id == c.id,
                LeadEvent.event_type == "visit_status_change",
            )
        )
        n_before = len(list(evt_q.scalars().all()))
        assert n_before == 1

        # Act: cancel.
        cancelled, err2 = await visit_service.cancel_visit(
            db, visit_id=visit.id, user_id=1,
        )
        assert err2 is None
        assert cancelled is not None
        assert cancelled.status == "cancelled"

        # Contact STILL in 'visit_scheduled' (Case C no-op).
        await db.refresh(c)
        assert c.status == "visit_scheduled"

        # No new lead_event emitted by the cancel call.
        evt_q2 = await db.execute(
            select(LeadEvent).where(
                LeadEvent.contact_id == c.id,
                LeadEvent.event_type == "visit_status_change",
            )
        )
        n_after = len(list(evt_q2.scalars().all()))
        assert n_after == n_before, (
            "cancel_visit must NOT emit a new visit_status_change event"
        )

        # No active visits remain.
        assert (await visit_repo.has_active_for_contact(db, c.id)) is False

    # ------------------------------------------------------------------
    # VISIT-04
    # ------------------------------------------------------------------
    async def test_reschedule_visit_is_atomic(self, db):
        """VISIT-04: reschedule cancels the original and inserts a new
        scheduled row. Contact stays in 'visit_scheduled'. NO new
        lead_event emitted by the reschedule itself.
        """
        c = await _make_contact(db, status="bot_replied")
        t0 = datetime.now(timezone.utc) + timedelta(days=1)
        v1, err = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=t0, agent_user_id=None,
        )
        assert err is None

        evt_q = await db.execute(
            select(LeadEvent).where(
                LeadEvent.contact_id == c.id,
                LeadEvent.event_type == "visit_status_change",
            )
        )
        n_before = len(list(evt_q.scalars().all()))
        assert n_before == 1

        new_at = t0 + timedelta(days=1)
        new_visit, err2 = await visit_service.reschedule_visit(
            db, visit_id=v1.id, scheduled_at=new_at, user_id=1,
            notes="rescheduled",
        )

        assert err2 is None
        assert new_visit is not None
        assert new_visit.id != v1.id
        assert new_visit.status == "scheduled"
        # Compare as UTC to avoid TZ vs local subtleties.
        assert new_visit.scheduled_at.astimezone(timezone.utc) == new_at

        # Old visit now cancelled.
        v1_refetched = await visit_repo.get_by_id(db, v1.id)
        assert v1_refetched is not None
        assert v1_refetched.status == "cancelled"

        # Contact still in visit_scheduled.
        await db.refresh(c)
        assert c.status == "visit_scheduled"

        # No new lead_event from the reschedule (only the original create one).
        evt_q2 = await db.execute(
            select(LeadEvent).where(
                LeadEvent.contact_id == c.id,
                LeadEvent.event_type == "visit_status_change",
            )
        )
        n_after = len(list(evt_q2.scalars().all()))
        assert n_after == n_before

    async def test_reschedule_visit_atomicity_smoke(self, monkeypatch):
        """VISIT-04 sub: if visit_repo.insert raises AFTER update_status
        cancels the original, the cancel is rolled back. Verified across
        two independent sessions so we can prove the abort propagated to
        the database (not just to in-memory ORM state).
        """
        from tests.conftest import _TestSession  # type: ignore[import-not-found]

        # Session A — set up the contact + visit, then commit so session B
        # can see them on a fresh connection.
        async with _TestSession() as setup:
            c = await _make_contact(setup, status="bot_replied")
            t0 = datetime.now(timezone.utc) + timedelta(days=1)
            v1, err = await visit_service.create_visit(
                setup, contact_id=c.id, scheduled_at=t0, agent_user_id=None,
            )
            assert err is None
            await setup.commit()
            v1_id = v1.id
            c_id = c.id

        # Session B — the reschedule attempt. Must rollback on failure.
        async with _TestSession() as work:
            original_insert = visit_repo.insert

            async def boom(*args, **kwargs):
                raise RuntimeError("boom")

            monkeypatch.setattr(visit_repo, "insert", boom)
            try:
                with pytest.raises(RuntimeError, match="boom"):
                    await visit_service.reschedule_visit(
                        work,
                        visit_id=v1_id,
                        scheduled_at=t0 + timedelta(days=2),
                        user_id=1,
                    )
                await work.rollback()
            finally:
                monkeypatch.setattr(visit_repo, "insert", original_insert)

        # Session C — fresh read; original visit must still be 'scheduled'.
        async with _TestSession() as check:
            v1_fresh = await visit_repo.get_by_id(check, v1_id)
            assert v1_fresh is not None
            assert v1_fresh.status == "scheduled", (
                "Reschedule failure must rollback the cancel of the original visit"
            )
            row = await check.execute(
                select(Contact.status).where(Contact.id == c_id)
            )
            assert row.scalar_one() == "visit_scheduled"


# ---------------------------------------------------------------------------
# VISIT-10 — future-date validation
# ---------------------------------------------------------------------------


class TestVisitServiceValidation:
    async def test_create_visit_rejects_past_date(self, db):
        """VISIT-10: create_visit with scheduled_at <= NOW() returns
        (None, 'La fecha debe ser futura') AND inserts no row.
        """
        c = await _make_contact(db, status="bot_replied")
        past = datetime.now(timezone.utc) - timedelta(hours=1)

        visit, error = await visit_service.create_visit(
            db,
            contact_id=c.id,
            scheduled_at=past,
            agent_user_id=None,
        )

        assert visit is None
        assert error == "La fecha debe ser futura"

        # No row inserted.
        rows = await db.execute(select(Visit).where(Visit.contact_id == c.id))
        assert list(rows.scalars().all()) == []

        # Contact status untouched.
        await db.refresh(c)
        assert c.status == "bot_replied"

    async def test_reschedule_visit_rejects_past_date(self, db):
        """VISIT-10b: reschedule with past date returns the same error and
        does NOT mutate the existing scheduled visit.
        """
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        v1, err = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future, agent_user_id=None,
        )
        assert err is None and v1 is not None
        original_status = v1.status

        past = datetime.now(timezone.utc) - timedelta(hours=1)
        new_visit, error = await visit_service.reschedule_visit(
            db,
            visit_id=v1.id,
            scheduled_at=past,
            user_id=1,
        )

        assert new_visit is None
        assert error == "La fecha debe ser futura"

        # Original visit untouched.
        v1_fresh = await visit_repo.get_by_id(db, v1.id)
        assert v1_fresh is not None
        assert v1_fresh.status == original_status == "scheduled"


# ---------------------------------------------------------------------------
# VISIT-11 — multiple active visits invariant
# ---------------------------------------------------------------------------


class TestVisitServiceMultipleActive:
    async def test_multiple_active_visits_keep_flag_until_all_terminal(self, db):
        """VISIT-11: with two scheduled visits, completing one leaves the
        contact in 'visit_scheduled' (the other keeps the flag). Completing
        the second ALSO leaves the contact in 'visit_scheduled' (Case C
        no-op — no auto-transition per VISIT-05). Throughout, only the
        ONE lead_event from the initial create is emitted.
        """
        c = await _make_contact(db, status="bot_replied")
        t = datetime.now(timezone.utc) + timedelta(days=1)

        v1, err1 = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=t, agent_user_id=None,
        )
        assert err1 is None

        v2, err2 = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=t + timedelta(hours=2),
            agent_user_id=None,
        )
        assert err2 is None

        # Baseline: only 1 lead_event (the first create flipped the status;
        # the second create's _sync was Case B no-op).
        evt_q = await db.execute(
            select(LeadEvent).where(
                LeadEvent.contact_id == c.id,
                LeadEvent.event_type == "visit_status_change",
            )
        )
        n_before = len(list(evt_q.scalars().all()))
        assert n_before == 1

        # Complete v1.
        done1, err3 = await visit_service.complete_visit(
            db, visit_id=v1.id, result="done", user_id=1,
        )
        assert err3 is None
        assert done1 is not None
        assert done1.status == "done"

        await db.refresh(c)
        assert c.status == "visit_scheduled", (
            "Other visit still scheduled → contact must keep the flag"
        )
        assert (await visit_repo.has_active_for_contact(db, c.id)) is True

        # Complete v2.
        done2, err4 = await visit_service.complete_visit(
            db, visit_id=v2.id, result="no_show", user_id=1,
        )
        assert err4 is None
        assert done2 is not None
        assert done2.status == "no_show"

        await db.refresh(c)
        assert c.status == "visit_scheduled", (
            "No auto-transition: contact stays in 'visit_scheduled' even after "
            "all visits are terminal (per VISIT-05)"
        )
        assert (await visit_repo.has_active_for_contact(db, c.id)) is False

        # Total lead_events unchanged throughout.
        evt_q2 = await db.execute(
            select(LeadEvent).where(
                LeadEvent.contact_id == c.id,
                LeadEvent.event_type == "visit_status_change",
            )
        )
        n_after = len(list(evt_q2.scalars().all()))
        assert n_after == n_before == 1


# ---------------------------------------------------------------------------
# list_visits_for_contact + error branches
# (No VISIT-id; pre-115-03 contract guards for 115-03 to depend on.)
# ---------------------------------------------------------------------------


class TestVisitServiceReadAndErrors:
    async def test_list_visits_for_contact_partitions_and_sorts(self, db):
        """list_visits_for_contact returns {proximas, historico}: scheduled
        items sorted ASC into 'proximas'; terminal items sorted DESC into
        'historico'.
        """
        c = await _make_contact(db, status="bot_replied")
        now = datetime.now(timezone.utc)

        # Two scheduled (future) — should land in 'proximas'.
        v_later, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=now + timedelta(days=2),
            agent_user_id=None,
        )
        v_sooner, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=now + timedelta(days=1),
            agent_user_id=None,
        )
        # One that we then complete (terminal → historico).
        v_done, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=now + timedelta(days=3),
            agent_user_id=None,
        )
        await visit_service.complete_visit(
            db, visit_id=v_done.id, result="done", user_id=1,
        )
        # One that we cancel (terminal → historico).
        v_cancel, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=now + timedelta(days=4),
            agent_user_id=None,
        )
        await visit_service.cancel_visit(
            db, visit_id=v_cancel.id, user_id=1,
        )

        result = await visit_service.list_visits_for_contact(
            db, contact_id=c.id,
        )

        assert set(result.keys()) == {"proximas", "historico"}
        proximas_ids = [v.id for v in result["proximas"]]
        historico_ids = [v.id for v in result["historico"]]

        # Proximas: ascending — sooner before later.
        assert proximas_ids == [v_sooner.id, v_later.id]
        # Historico: descending by scheduled_at — v_cancel (day+4) before v_done (day+3).
        assert historico_ids == [v_cancel.id, v_done.id]

    async def test_list_visits_for_contact_empty(self, db):
        """No visits → both lists empty (read-only is fine)."""
        c = await _make_contact(db, status="bot_replied")
        result = await visit_service.list_visits_for_contact(
            db, contact_id=c.id,
        )
        assert result == {"proximas": [], "historico": []}

    async def test_create_visit_unknown_contact(self, db):
        future = datetime.now(timezone.utc) + timedelta(days=1)
        visit, error = await visit_service.create_visit(
            db,
            contact_id=999_999_999,
            scheduled_at=future,
            agent_user_id=None,
        )
        assert visit is None
        assert error == "Contacto no encontrado"

    async def test_create_visit_invalid_source(self, db):
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        visit, error = await visit_service.create_visit(
            db,
            contact_id=c.id,
            scheduled_at=future,
            agent_user_id=None,
            source="garbage",
        )
        assert visit is None
        assert error == "Source inválido"

    async def test_create_visit_unknown_property(self, db):
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        visit, error = await visit_service.create_visit(
            db,
            contact_id=c.id,
            scheduled_at=future,
            agent_user_id=None,
            property_id=999_999_999,
        )
        assert visit is None
        assert error == "Propiedad no encontrada"

    async def test_cancel_visit_unknown(self, db):
        visit, error = await visit_service.cancel_visit(
            db, visit_id=999_999_999, user_id=1,
        )
        assert visit is None
        assert error == "Visita no encontrada"

    async def test_cancel_visit_already_cancelled(self, db):
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        v, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future, agent_user_id=None,
        )
        await visit_service.cancel_visit(db, visit_id=v.id, user_id=1)

        # Second cancel → guarded.
        visit, error = await visit_service.cancel_visit(
            db, visit_id=v.id, user_id=1,
        )
        assert visit is None
        assert error == "Visita ya está cancelada"

    async def test_cancel_visit_already_done(self, db):
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        v, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future, agent_user_id=None,
        )
        await visit_service.complete_visit(
            db, visit_id=v.id, result="done", user_id=1,
        )

        visit, error = await visit_service.cancel_visit(
            db, visit_id=v.id, user_id=1,
        )
        assert visit is None
        assert error == "Visita ya completada"

    async def test_complete_visit_invalid_result(self, db):
        visit, error = await visit_service.complete_visit(
            db, visit_id=1, result="bogus", user_id=1,
        )
        assert visit is None
        assert error == "Result inválido"

    async def test_complete_visit_unknown(self, db):
        visit, error = await visit_service.complete_visit(
            db, visit_id=999_999_999, result="done", user_id=1,
        )
        assert visit is None
        assert error == "Visita no encontrada"

    async def test_complete_visit_not_scheduled(self, db):
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        v, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future, agent_user_id=None,
        )
        await visit_service.cancel_visit(db, visit_id=v.id, user_id=1)

        visit, error = await visit_service.complete_visit(
            db, visit_id=v.id, result="done", user_id=1,
        )
        assert visit is None
        assert error == "Visita no está en scheduled"

    async def test_reschedule_visit_unknown(self, db):
        future = datetime.now(timezone.utc) + timedelta(days=1)
        visit, error = await visit_service.reschedule_visit(
            db, visit_id=999_999_999, scheduled_at=future, user_id=1,
        )
        assert visit is None
        assert error == "Visita no encontrada"

    async def test_reschedule_visit_not_scheduled(self, db):
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        v, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future, agent_user_id=None,
        )
        await visit_service.cancel_visit(db, visit_id=v.id, user_id=1)

        visit, error = await visit_service.reschedule_visit(
            db, visit_id=v.id, scheduled_at=future + timedelta(days=1),
            user_id=1,
        )
        assert visit is None
        assert error == "Visita no está en scheduled"


# ===========================================================================
# Phase 116 UAT fix-forward — per-action visit events
# ===========================================================================


class TestVisitServicePerActionEvents:
    """Each visit lifecycle action emits a distinct lead_event so the
    timeline mirrors the per-action behaviour of notes (Phase 116 fix).

    Before this change only `visit_status_change` was emitted (once, when
    contact.status flipped to 'visit_scheduled'), leaving cancel / complete
    / reschedule invisible in the timeline.
    """

    async def _events_for_contact(self, db, contact_id):
        result = await db.execute(
            select(LeadEvent)
            .where(LeadEvent.contact_id == contact_id)
            .order_by(LeadEvent.id.asc())
        )
        return list(result.scalars().all())

    async def test_create_visit_emits_visit_created_event(self, db):
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        v, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future, agent_user_id=None,
        )

        events = await self._events_for_contact(db, c.id)
        action_events = [e for e in events if e.event_type == "visit_created"]
        assert len(action_events) == 1
        assert action_events[0].event_metadata.get("visit_id") == v.id

    async def test_cancel_visit_emits_visit_cancelled_event(self, db):
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        v, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future, agent_user_id=None,
        )
        await visit_service.cancel_visit(db, visit_id=v.id, user_id=42)

        events = await self._events_for_contact(db, c.id)
        action_events = [e for e in events if e.event_type == "visit_cancelled"]
        assert len(action_events) == 1
        assert action_events[0].event_metadata.get("visit_id") == v.id
        assert action_events[0].triggered_by == "user:42"

    async def test_complete_done_emits_event_with_result_done(self, db):
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        v, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future, agent_user_id=None,
        )
        await visit_service.complete_visit(
            db, visit_id=v.id, result="done", user_id=42,
        )

        events = await self._events_for_contact(db, c.id)
        completed = [e for e in events if e.event_type == "visit_completed"]
        assert len(completed) == 1
        assert completed[0].event_metadata.get("result") == "done"

    async def test_complete_no_show_emits_event_with_result_no_show(self, db):
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        v, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future, agent_user_id=None,
        )
        await visit_service.complete_visit(
            db, visit_id=v.id, result="no_show", user_id=42,
        )

        events = await self._events_for_contact(db, c.id)
        completed = [e for e in events if e.event_type == "visit_completed"]
        assert len(completed) == 1
        assert completed[0].event_metadata.get("result") == "no_show"

    async def test_reschedule_emits_visit_rescheduled_event(self, db):
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        v, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future, agent_user_id=None,
        )
        new_dt = future + timedelta(days=2)
        new_v, _ = await visit_service.reschedule_visit(
            db, visit_id=v.id, scheduled_at=new_dt, user_id=42,
        )

        events = await self._events_for_contact(db, c.id)
        rescheduled = [e for e in events if e.event_type == "visit_rescheduled"]
        assert len(rescheduled) == 1
        assert rescheduled[0].event_metadata.get("old_visit_id") == v.id
        assert rescheduled[0].event_metadata.get("visit_id") == new_v.id

    async def test_create_then_cancel_emits_both_events_in_order(self, db):
        """Regression bar: the UAT observed 1 event for 5 actions; this
        asserts every action contributes its own row, in order."""
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        v, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future, agent_user_id=None,
        )
        await visit_service.cancel_visit(db, visit_id=v.id, user_id=42)
        v2, _ = await visit_service.create_visit(
            db,
            contact_id=c.id,
            scheduled_at=future + timedelta(days=3),
            agent_user_id=None,
        )
        await visit_service.complete_visit(
            db, visit_id=v2.id, result="no_show", user_id=42,
        )

        events = await self._events_for_contact(db, c.id)
        action_types = [e.event_type for e in events
                        if e.event_type.startswith("visit_")]
        # The exact sequence we expect:
        assert action_types[:5] == [
            "visit_created",            # v
            "visit_status_change",      # contact new → visit_scheduled
            "visit_cancelled",          # v
            "visit_created",            # v2
            "visit_completed",          # v2 (no_show)
        ]


# ===========================================================================
# Phase 116 — reschedule notes-diff in event metadata
# ===========================================================================


class TestVisitServiceRescheduleNotesDiff:
    async def _events_for_contact(self, db, contact_id):
        result = await db.execute(
            select(LeadEvent)
            .where(LeadEvent.contact_id == contact_id)
            .order_by(LeadEvent.id.asc())
        )
        return list(result.scalars().all())

    async def test_reschedule_with_changed_notes_records_diff(self, db):
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        v, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future,
            agent_user_id=None, notes="original notes",
        )
        await visit_service.reschedule_visit(
            db, visit_id=v.id,
            scheduled_at=future + timedelta(days=2),
            user_id=42,
            notes="updated notes",
        )

        events = await self._events_for_contact(db, c.id)
        rescheduled = [e for e in events if e.event_type == "visit_rescheduled"]
        assert len(rescheduled) == 1
        meta = rescheduled[0].event_metadata
        assert meta.get("old_notes") == "original notes"
        assert meta.get("new_notes") == "updated notes"

    async def test_reschedule_without_notes_change_omits_diff(self, db):
        """When only the date moved, the metadata stays slim — no
        old_notes/new_notes keys are emitted."""
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        v, _ = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future,
            agent_user_id=None, notes="unchanged",
        )
        # notes=None → service falls back to original_notes
        await visit_service.reschedule_visit(
            db, visit_id=v.id,
            scheduled_at=future + timedelta(days=2),
            user_id=42,
        )

        events = await self._events_for_contact(db, c.id)
        rescheduled = [e for e in events if e.event_type == "visit_rescheduled"]
        meta = rescheduled[0].event_metadata
        assert "old_notes" not in meta
        assert "new_notes" not in meta


# ===========================================================================
# STAB-07 (TD-116-01) — soft-deleting a contact cancels its scheduled visits
# ===========================================================================


class TestSoftDeleteCancelsScheduledVisits:
    """STAB-07: `contact_service.delete_contact` soft-deletes a contact but
    must NOT leave any `scheduled` visit dangling — otherwise an agent sees a
    phantom scheduled visit for a deleted lead.

    On soft-delete, every scheduled visit for that contact must be:
      - status='cancelled'
      - notes='contact deleted'
      - accompanied by a `visit_cancelled` lead_event.

    Contacts with no scheduled visits must soft-delete unchanged.
    """

    async def _events_for_contact(self, db, contact_id):
        result = await db.execute(
            select(LeadEvent)
            .where(LeadEvent.contact_id == contact_id)
            .order_by(LeadEvent.id.asc())
        )
        return list(result.scalars().all())

    async def test_soft_delete_cancels_scheduled_visit(self, db):
        """A scheduled visit is cancelled (status + notes) and a
        visit_cancelled lead_event is emitted when its contact is soft-deleted.
        """
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        visit, err = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future, agent_user_id=None,
        )
        assert err is None and visit is not None
        assert visit.status == "scheduled"

        ok, error = await contact_service.delete_contact(
            db,
            contact_id=c.id,
            user_id=1,
            user_email="pytest_admin@example.com",
            user_role="admin",
        )
        assert ok is True
        assert error is None

        # Visit cancelled with the deletion note.
        refetched = await visit_repo.get_by_id(db, visit.id)
        assert refetched is not None
        assert refetched.status == "cancelled"
        assert refetched.notes == "contact deleted"

        # A visit_cancelled lead_event exists for this contact.
        events = await self._events_for_contact(db, c.id)
        cancelled_events = [
            e for e in events if e.event_type == "visit_cancelled"
        ]
        assert len(cancelled_events) == 1
        assert cancelled_events[0].event_metadata.get("visit_id") == visit.id

        # Contact ends up soft-deleted (deleted wins over visit_scheduled).
        await db.refresh(c)
        assert c.status == "deleted"

    async def test_soft_delete_without_scheduled_visit_is_unchanged(self, db):
        """A contact with no scheduled visit soft-deletes normally — returns
        (True, None), emits the `deleted` event but NO `visit_cancelled`.
        """
        c = await _make_contact(db, status="bot_replied")

        ok, error = await contact_service.delete_contact(
            db,
            contact_id=c.id,
            user_id=1,
            user_email="pytest_admin@example.com",
            user_role="admin",
        )
        assert ok is True
        assert error is None

        events = await self._events_for_contact(db, c.id)
        cancelled_events = [
            e for e in events if e.event_type == "visit_cancelled"
        ]
        assert cancelled_events == []
        deleted_events = [e for e in events if e.event_type == "deleted"]
        assert len(deleted_events) == 1

        await db.refresh(c)
        assert c.status == "deleted"

    async def test_soft_delete_does_not_recancel_terminal_visits(self, db):
        """A visit already in a terminal state (cancelled/done) is NOT
        re-cancelled or re-noted, and emits no extra visit_cancelled event.
        """
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        v, err = await visit_service.create_visit(
            db, contact_id=c.id, scheduled_at=future, agent_user_id=None,
        )
        assert err is None and v is not None
        # Move the visit to a terminal state BEFORE deleting the contact.
        await visit_service.complete_visit(
            db, visit_id=v.id, result="done", user_id=1,
        )

        events_before = await self._events_for_contact(db, c.id)
        n_cancelled_before = len(
            [e for e in events_before if e.event_type == "visit_cancelled"]
        )

        ok, error = await contact_service.delete_contact(
            db,
            contact_id=c.id,
            user_id=1,
            user_email="pytest_admin@example.com",
            user_role="admin",
        )
        assert ok is True and error is None

        refetched = await visit_repo.get_by_id(db, v.id)
        assert refetched is not None
        # Terminal visit untouched.
        assert refetched.status == "done"
        assert refetched.notes != "contact deleted"

        events_after = await self._events_for_contact(db, c.id)
        n_cancelled_after = len(
            [e for e in events_after if e.event_type == "visit_cancelled"]
        )
        assert n_cancelled_after == n_cancelled_before

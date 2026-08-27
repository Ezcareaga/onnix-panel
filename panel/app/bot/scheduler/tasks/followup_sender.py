"""FollowupSender — sends follow-up templates to unresponsive contacts.

Two-stage follow-up logic:
- Stage 24h: sends wa_tpl_followup_v3 to contacts silent for 24h with 0 prior followups
- Stage 72h: sends wa_tpl_followup_72h_v3 to contacts silent for 72h+ with 1 prior followup
- Stage 96h: marks contacts as 'discarded' after 96h+ with 2+ prior followups (no template)

Compliance rules from bot_settings:
- followup_max_attempts (default: 3) — max total follow-up sends per contact
- followup_cooldown_hours (default: 48) — hours between follow-up sends
- max_template_per_day (default: 1) — max templates per contact per day
- followup_24h_enabled (default: true) — enable 24h stage
- followup_72h_enabled (default: true) — enable 72h stage
- followup_96h_discard (default: false) — enable 96h auto-discard stage

Guard: set FOLLOWUP_SENDER_ENABLED=false to disable without redeploying
(use in staging to prevent real WA sends from non-prod environments).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.database import async_session_factory
from app.models.lead_event import LeadEvent
from app.repositories.bot_setting_repo import BotSettingRepository
from app.services.template_service import template_service

logger = logging.getLogger(__name__)

_TEMPLATE_KEY = "wa_tpl_followup_v3"
_TEMPLATE_KEY_72H = "wa_tpl_followup_72h_v3"
_CANDIDATE_STATUSES = ("no_response", "bot_replied")
_CANDIDATE_SOURCES = ("infocasas", "whatsapp")
_CANDIDATE_LIMIT = 100

# Timing thresholds for stage determination
_STAGE_24H_MIN_HOURS = 24
_STAGE_72H_MIN_HOURS = 72
_STAGE_COOLDOWN_HOURS = 48  # min hours between two followup sends


class FollowupSender:
    """Finds eligible contacts and sends follow-up templates via TemplateService.

    Two-stage logic:
    - 24h without response → wa_tpl_followup_v3
    - 72h without response → wa_tpl_followup_72h_v3
    - 96h without response → mark contact as discarded (no template)

    Parameters
    ----------
    session_factory:
        Optional async session factory override (for testing).
    """

    def __init__(self, *, session_factory=None) -> None:
        self._session_factory = session_factory or async_session_factory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> dict:
        """Execute one follow-up sending cycle.

        Returns a dict with ``candidates``, ``sent``, ``skipped``,
        ``discarded``, and ``errors`` counts.
        """
        start = time.monotonic()

        # --- Guard: staging isolation (env var) ---
        if os.getenv("FOLLOWUP_SENDER_ENABLED", "true").lower() == "false":
            logger.info(
                "FollowupSender: disabled via FOLLOWUP_SENDER_ENABLED=false — skipping"
            )
            return {"skipped": True, "reason": "FOLLOWUP_SENDER_ENABLED=false"}

        # --- Guard: DB setting — fail-safe default (disabled if key missing) ---
        try:
            async with self._session_factory() as _s:
                db_enabled = await BotSettingRepository.get_value(
                    _s, "scheduler_followup_sender_enabled"
                )
        except Exception:
            logger.warning("FollowupSender: DB error reading enabled flag — skipping")
            return {"skipped": True, "reason": "db_error_reading_enabled"}
        _TRUTHY = {"true", "1", "yes"}
        if db_enabled is None or db_enabled.strip().lower() not in _TRUTHY:
            logger.info(
                "FollowupSender: disabled via scheduler_followup_sender_enabled=%r — skipping",
                db_enabled,
            )
            return {"skipped": True, "reason": "scheduler_followup_sender_enabled not enabled"}

        # --- Read compliance settings from bot_settings ---
        (
            max_attempts,
            cooldown_hours,
            max_per_day,
            stage_24h_enabled,
            stage_72h_enabled,
            stage_96h_enabled,
        ) = await self._load_compliance_settings()

        # --- Read 72h template key value (SID) — may be None if not yet configured ---
        tpl_72h_value = await self._load_72h_template_value()

        # --- Query candidates ---
        candidates = await self._fetch_candidates()
        if not candidates:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.info(
                'Job executed — {"task": "followup_sender", "duration_ms": %.0f, '
                '"candidates": 0, "sent": 0, "skipped": 0, "discarded": 0, "errors": 0}',
                elapsed_ms,
            )
            return {"candidates": 0, "sent": 0, "skipped": 0, "discarded": 0, "errors": 0}

        sent = 0
        skipped = 0
        discarded = 0
        errors = 0

        for contact_id, contact_name, contact_phone in candidates:
            eligible, stage, skip_reason = await self._check_compliance_v2(contact_id)

            if not eligible and stage == "96h":
                # Auto-discard stage
                if stage_96h_enabled:
                    await self._discard_contact(contact_id, contact_phone)
                    discarded += 1
                else:
                    logger.debug(
                        "FollowupSender: skip contact %d — 96h but discard stage disabled",
                        contact_id,
                    )
                    skipped += 1
                continue

            if not eligible:
                logger.debug(
                    "FollowupSender: skip contact %d — %s", contact_id, skip_reason
                )
                skipped += 1
                continue

            # Determine template key for this stage
            if stage == "24h":
                if not stage_24h_enabled:
                    logger.debug(
                        "FollowupSender: skip contact %d — 24h stage disabled", contact_id
                    )
                    skipped += 1
                    continue
                template_key = _TEMPLATE_KEY

            elif stage == "72h":
                if not stage_72h_enabled:
                    logger.debug(
                        "FollowupSender: skip contact %d — 72h stage disabled", contact_id
                    )
                    skipped += 1
                    continue
                if not tpl_72h_value:
                    logger.warning(
                        "FollowupSender: skip contact %d — %s not configured in bot_settings",
                        contact_id,
                        _TEMPLATE_KEY_72H,
                    )
                    skipped += 1
                    continue
                template_key = _TEMPLATE_KEY_72H

            else:
                # Unknown/None stage — skip
                logger.debug(
                    "FollowupSender: skip contact %d — no eligible stage", contact_id
                )
                skipped += 1
                continue

            # --- Send template in its own session ---
            try:
                async with self._session_factory() as session:
                    await template_service.send_template(
                        session, contact_id, template_key
                    )

                    event = LeadEvent(
                        contact_id=contact_id,
                        event_type="followup_sent",
                        old_status=None,
                        new_status=None,
                        triggered_by="followup_sender",
                        event_metadata={"template_key": template_key, "stage": stage},
                    )
                    session.add(event)
                    await session.commit()

                logger.info(
                    "FollowupSender: sent %s (stage=%s) to contact %d (%s)",
                    template_key,
                    stage,
                    contact_id,
                    contact_phone,
                )
                sent += 1

            except Exception:
                logger.warning(
                    "FollowupSender: failed to send to contact %d",
                    contact_id,
                    exc_info=True,
                )
                errors += 1

            await asyncio.sleep(15)

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            'Job executed — {"task": "followup_sender", "duration_ms": %.0f, '
            '"candidates": %d, "sent": %d, "skipped": %d, "discarded": %d, "errors": %d}',
            elapsed_ms,
            len(candidates),
            sent,
            skipped,
            discarded,
            errors,
        )
        return {
            "candidates": len(candidates),
            "sent": sent,
            "skipped": skipped,
            "discarded": discarded,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Public helper: stage determination (pure, no DB)
    # ------------------------------------------------------------------

    def _determine_stage(
        self,
        *,
        total_sent: int,
        last_sent: datetime | None,
        last_activity_at: datetime | None,
    ) -> str | None:
        """Determine which follow-up stage applies to a contact.

        Returns '24h', '72h', '96h', or None (not eligible yet).

        Stage rules:
        - 0 followups sent + last_activity_at >= 24h ago → '24h'
        - 1 followup sent + last followup sent >= 48h ago → '72h'
        - 2+ followups sent → '96h' (discard candidate)
        - last_activity_at is None → None (cannot determine)

        Parameters
        ----------
        total_sent:
            Number of followup_sent lead_events for this contact.
        last_sent:
            Timestamp of the most recent followup_sent event.
        last_activity_at:
            Contact's last_activity_at timestamp.
        """
        now = datetime.now(timezone.utc)

        if last_activity_at is None:
            return None

        # Ensure timezone-aware
        if last_activity_at.tzinfo is None:
            last_activity_at = last_activity_at.replace(tzinfo=timezone.utc)

        hours_since_activity = (now - last_activity_at).total_seconds() / 3600

        if total_sent == 0:
            if hours_since_activity >= _STAGE_24H_MIN_HOURS:
                return "24h"
            return None

        if total_sent == 1:
            if last_sent is None:
                return None
            if last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=timezone.utc)
            hours_since_last_send = (now - last_sent).total_seconds() / 3600
            if hours_since_last_send >= _STAGE_COOLDOWN_HOURS:
                return "72h"
            return None

        # 2+ followups already sent → discard candidate
        return "96h"

    # ------------------------------------------------------------------
    # Private: compliance check v2 (stage-aware)
    # ------------------------------------------------------------------

    async def _check_compliance_v2(
        self,
        contact_id: int,
    ) -> tuple[bool, str | None, str]:
        """Check whether a contact is eligible and determine its stage.

        Returns (eligible, stage, reason_if_not).

        eligible=True  → send template for `stage`
        eligible=False, stage='96h' → discard candidate
        eligible=False, stage=None → not yet due for any followup

        Fail-safe: if the DB query fails, returns (False, None, 'compliance check error').
        """
        sql = text(
            """
            SELECT
                le.total_sent,
                le.last_sent,
                c.last_activity_at
            FROM contacts c
            LEFT JOIN (
                SELECT
                    contact_id,
                    COUNT(*) AS total_sent,
                    MAX(created_at) AS last_sent
                FROM lead_events
                WHERE event_type = 'followup_sent'
                GROUP BY contact_id
            ) le ON le.contact_id = c.id
            WHERE c.id = :contact_id
            """
        )
        try:
            async with self._session_factory() as session:
                result = await session.execute(sql, {"contact_id": contact_id})
                row = result.fetchone()

            if row is None:
                return False, None, "contact not found"

            total_sent: int = int(row[0]) if row[0] is not None else 0
            last_sent: datetime | None = row[1]
            last_activity_at: datetime | None = row[2]

            stage = self._determine_stage(
                total_sent=total_sent,
                last_sent=last_sent,
                last_activity_at=last_activity_at,
            )

            if stage is None:
                return False, None, "not yet due for any followup stage"

            if stage == "96h":
                return False, "96h", f"max_attempts reached ({total_sent}/2)"

            # 24h or 72h → eligible for template
            return True, stage, ""

        except Exception:
            logger.warning(
                "FollowupSender: compliance check failed for contact %d — skipping (safe)",
                contact_id,
                exc_info=True,
            )
            return False, None, "compliance check error"

    # ------------------------------------------------------------------
    # Private: discard a contact at the 96h stage
    # ------------------------------------------------------------------

    async def _discard_contact(self, contact_id: int, contact_phone: str) -> None:
        """Mark a contact as discarded and record an auto_discarded_96h event."""
        try:
            async with self._session_factory() as session:
                await session.execute(
                    text("UPDATE contacts SET status = 'discarded' WHERE id = :id"),
                    {"id": contact_id},
                )
                event = LeadEvent(
                    contact_id=contact_id,
                    event_type="auto_discarded_96h",
                    old_status=None,
                    new_status="discarded",
                    triggered_by="followup_sender",
                    event_metadata={"reason": "no_response_96h"},
                )
                session.add(event)
                await session.commit()

            logger.info(
                "FollowupSender: discarded contact %d (%s) after 96h no-response",
                contact_id,
                contact_phone,
            )
        except Exception:
            logger.warning(
                "FollowupSender: failed to discard contact %d",
                contact_id,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Private: compliance settings
    # ------------------------------------------------------------------

    async def _load_compliance_settings(
        self,
    ) -> tuple[int, int, int, bool, bool, bool]:
        """Read followup compliance settings from bot_settings.

        Returns (max_attempts, cooldown_hours, max_per_day,
                 stage_24h_enabled, stage_72h_enabled, stage_96h_enabled).
        Falls back to safe defaults if keys are missing or unreadable.
        """
        _TRUTHY = {"true", "1", "yes"}
        defaults = (3, 48, 1, True, True, False)
        try:
            async with self._session_factory() as session:
                raw_max_attempts = await BotSettingRepository.get_value(
                    session, "followup_max_attempts"
                )
                raw_cooldown = await BotSettingRepository.get_value(
                    session, "followup_cooldown_hours"
                )
                raw_per_day = await BotSettingRepository.get_value(
                    session, "max_template_per_day"
                )
                raw_24h = await BotSettingRepository.get_value(
                    session, "followup_24h_enabled"
                )
                raw_72h = await BotSettingRepository.get_value(
                    session, "followup_72h_enabled"
                )
                raw_96h = await BotSettingRepository.get_value(
                    session, "followup_96h_discard"
                )

            max_attempts = int(raw_max_attempts) if raw_max_attempts else 3
            cooldown_hours = int(raw_cooldown) if raw_cooldown else 48
            max_per_day = int(raw_per_day) if raw_per_day else 1
            stage_24h_enabled = (raw_24h or "true").strip().lower() in _TRUTHY
            stage_72h_enabled = (raw_72h or "true").strip().lower() in _TRUTHY
            stage_96h_enabled = (raw_96h or "false").strip().lower() in _TRUTHY
            return (
                max_attempts,
                cooldown_hours,
                max_per_day,
                stage_24h_enabled,
                stage_72h_enabled,
                stage_96h_enabled,
            )

        except Exception:
            logger.warning(
                "FollowupSender: failed to read compliance settings, using defaults %r",
                defaults,
                exc_info=True,
            )
            return defaults

    async def _load_72h_template_value(self) -> str | None:
        """Load the SID/value for the 72h template from bot_settings.

        Returns None if the key is not configured (template not yet created).
        """
        try:
            async with self._session_factory() as session:
                value = await BotSettingRepository.get_value(
                    session, _TEMPLATE_KEY_72H
                )
            return value or None
        except Exception:
            logger.warning(
                "FollowupSender: failed to read %s from bot_settings — treating as missing",
                _TEMPLATE_KEY_72H,
            )
            return None

    # ------------------------------------------------------------------
    # Private: query candidates
    # ------------------------------------------------------------------

    async def _fetch_candidates(self) -> list[tuple[int, str | None, str]]:
        """Return list of (contact_id, name, phone) for eligible contacts."""
        sql = text(
            """
            SELECT c.id, c.name, c.phone
            FROM contacts c
            WHERE c.status IN :statuses
              AND c.phone IS NOT NULL
              AND c.baja_at IS NULL
              AND c.source IN :sources
            ORDER BY c.last_activity_at ASC NULLS FIRST
            LIMIT :lim
            """
        )
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    sql,
                    {
                        "statuses": tuple(_CANDIDATE_STATUSES),
                        "sources": tuple(_CANDIDATE_SOURCES),
                        "lim": _CANDIDATE_LIMIT,
                    },
                )
                return [(row[0], row[1], row[2]) for row in result.fetchall()]
        except Exception:
            logger.exception("FollowupSender: failed to fetch candidates")
            return []


# ------------------------------------------------------------------
# Module-level factory
# ------------------------------------------------------------------


async def run_followup_sender() -> dict:
    """Factory function invoked by the scheduler.

    Creates a fresh FollowupSender each invocation.
    Skips silently when FOLLOWUP_SENDER_ENABLED=false (staging guard).
    """
    sender = FollowupSender()
    return await sender.run()

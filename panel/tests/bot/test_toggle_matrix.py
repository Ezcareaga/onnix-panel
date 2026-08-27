"""Toggle matrix tests for InfocasasService — Interpretation B.

Covers all 12 combinations of:
  - ic_autoreply_enabled        ("true" / "false")
  - ic_autoreply_reenviados_enabled ("true" / "false")
  - lead_type                   (direct_match / direct_no_match / reenviado)

Expected actions:
  - "skip"           → neither _send_whatsapp_welcome nor _send_whatsapp_reenviado_welcome called
  - "send_welcome"   → _send_whatsapp_welcome called once
  - "send_reenviado" → _send_whatsapp_reenviado_welcome called once
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.services.infocasas.infocasas_service import InfocasasService
from app.bot.services.infocasas.notification_fetcher import NotificationFetcher
from app.bot.services.infocasas.session_manager import SessionManager
from app.bot.services.infocasas.lead_parser import ParsedLead
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Matrix definition
# ---------------------------------------------------------------------------

TOGGLE_MATRIX = [
    # (ic_autoreply_enabled, ic_autoreply_reenviados_enabled, lead_type, expected_method)
    #
    # NOTE on direct_match: _process_lead always delegates to _send_whatsapp_welcome
    # regardless of ic_autoreply_enabled — the toggle check is INSIDE that method.
    # So expected_method for direct_match is always "send_welcome" at the call level.
    # The internal skip-when-disabled behaviour is tested in TestSendWhatsappWelcome.
    ("false", "false", "direct_match",    "send_welcome"),
    ("false", "false", "direct_no_match", "skip"),
    ("false", "false", "reenviado",       "skip"),
    ("false", "true",  "direct_match",    "send_welcome"),
    ("false", "true",  "direct_no_match", "send_reenviado"),
    ("false", "true",  "reenviado",       "send_reenviado"),
    ("true",  "false", "direct_match",    "send_welcome"),
    ("true",  "false", "direct_no_match", "skip"),
    ("true",  "false", "reenviado",       "skip"),
    ("true",  "true",  "direct_match",    "send_welcome"),
    ("true",  "true",  "direct_no_match", "send_reenviado"),
    ("true",  "true",  "reenviado",       "send_reenviado"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_parsed_lead(*, is_reassigned: bool) -> ParsedLead:
    return ParsedLead(
        consulta_id="test-001",
        name="Test User",
        phone="+595991000001",
        email="test@example.com",
        message="Hola",
        consulta_date=datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc),
        property_code="OF99ZZ",
        property_title="Casa en Luque",
        listing_city="Luque",
        has_whatsapp=True,
        is_reassigned=is_reassigned,
        listing_type=None,
        listing_operation=None,
        listing_bedrooms=None,
        listing_area_m2=None,
        listing_price=None,
        listing_currency=None,
    )


def _make_service() -> InfocasasService:
    """Build an InfocasasService with fully mocked external dependencies."""
    sm = MagicMock(spec=SessionManager)
    sm.get_valid_token = AsyncMock(return_value="tok")
    nf = MagicMock(spec=NotificationFetcher)

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    session_factory = MagicMock(return_value=mock_ctx)

    return InfocasasService(
        session_manager=sm,
        notification_fetcher=nf,
        notifier=None,
        session_factory=session_factory,
    )


def _make_contact() -> MagicMock:
    from app.models.contact import Contact
    contact = MagicMock(spec=Contact)
    contact.id = 42
    return contact


# ---------------------------------------------------------------------------
# Parametrized test class
# ---------------------------------------------------------------------------


class TestToggleMatrix:
    """All 12 combinations of the two IC toggle settings and three lead types."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "ic_autoreply_enabled, ic_autoreply_reenviados_enabled, lead_type, expected_action",
        TOGGLE_MATRIX,
        ids=[
            f"autoreply={r[0]},reenviados={r[1]},type={r[2]}"
            for r in TOGGLE_MATRIX
        ],
    )
    async def test_toggle_matrix(
        self,
        ic_autoreply_enabled: str,
        ic_autoreply_reenviados_enabled: str,
        lead_type: str,
        expected_action: str,
    ) -> None:
        """Assert that the correct send method (or none) is called for each combination."""
        is_reassigned = lead_type == "reenviado"
        parsed = _make_parsed_lead(is_reassigned=is_reassigned)
        contact = _make_contact()

        svc = _make_service()

        # matched_property: not None for direct_match, None otherwise
        matched_property = {"city": "Luque"} if lead_type == "direct_match" else None

        async def fake_get_value(session, key: str) -> str | None:
            if key == "ic_autoreply_enabled":
                return ic_autoreply_enabled
            if key == "ic_autoreply_reenviados_enabled":
                return ic_autoreply_reenviados_enabled
            return None

        with (
            patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert,
            patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match,
            patch.object(svc, "_log_lead_event", new_callable=AsyncMock),
            patch.object(svc, "_notify_new_lead", new_callable=AsyncMock),
            patch.object(svc, "_send_whatsapp_welcome", new_callable=AsyncMock) as mock_welcome,
            patch.object(svc, "_send_whatsapp_reenviado_welcome", new_callable=AsyncMock) as mock_reenviado,
            patch(
                "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                new=fake_get_value,
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.parse_lead",
                return_value=parsed,
            ),
            patch.object(
                svc._fetcher,
                "fetch_lead_details",
                new_callable=AsyncMock,
                return_value={"id": "test-001"},
            ),
        ):
            mock_upsert.return_value = (True, True, contact)
            mock_match.return_value = matched_property

            await svc._process_lead("tok", "test-001")

        if expected_action == "send_welcome":
            mock_welcome.assert_called_once()
            mock_reenviado.assert_not_called()
        elif expected_action == "send_reenviado":
            mock_reenviado.assert_called_once()
            mock_welcome.assert_not_called()
        else:  # "skip"
            mock_welcome.assert_not_called()
            mock_reenviado.assert_not_called()

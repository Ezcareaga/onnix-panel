"""TDD — M6.4: build_origin_context with vista_publica numeric prop codes.

The public-site CTA sends:
    "Hola! Me interesa la propiedad {code} que vi en onnix.com.py"
where ``code`` is the `properties.id` zero-padded to 5 digits (e.g. "00060",
"1977528"). The IC lookup (`get_ic_by_ref`) misses for these numeric refs;
the implementation must then fall through to `PropertyRepository.get_full_detail`
to resolve the title from the `properties` table.

Test coverage:
  1. ref "00060" + IC miss + property id=60 with title → note contains title +
     "CÓDIGO 00060" (DIRECTO note with title).
  2. ref numeric + IC miss + property also absent → CÓDIGO-only note (unchanged
     behaviour, no crash).
  3. ref alphanumeric "EC1754" (IC hit) → IC path resolves title, get_full_detail
     is NOT called (regression guard for existing IC flow).
  4. exception inside get_full_detail → defensive: no crash, falls to CÓDIGO-only.
  5. detector consistency (E2E-lite): the exact CTA messages with numeric codes
     "00060" and "1977528" are detected and extracted correctly by the pure-function
     detector (_is_vista_publica_handshake / _extract_prop_code).

All DB / repository calls are mocked. No live DB, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure panel/ is on sys.path.
_panel_dir = str(Path(__file__).resolve().parent.parent.parent)
if _panel_dir not in sys.path:
    sys.path.insert(0, _panel_dir)

from app.bot.core.conversation import (  # noqa: E402
    ConversationManager,
    _extract_prop_code,
    _is_vista_publica_handshake,
)
from app.bot.core.types import ContactInfo  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_contact(
    *,
    contact_id: int = 1,
    infocasas_ref: str | None = None,
    source: str = "vista_publica",
) -> ContactInfo:
    return ContactInfo(
        id=contact_id,
        name="Test User",
        status="new",
        is_baja=False,
        platform="whatsapp",
        phone="+595981599900",
        source_id="+595981599900",
        source=source,
        infocasas_ref=infocasas_ref,
    )


def _mock_session_with_no_preferences() -> AsyncMock:
    """AsyncSession mock that returns no preferences row (directo path)."""
    session = AsyncMock()
    # Simulate the preferences SELECT in _build_indirecto_note returning None
    # (no reenviada row → fall through to DIRECTO).
    pref_result = MagicMock()
    pref_result.first.return_value = None
    session.execute.return_value = pref_result
    return session


def _mock_ic_prop(title: str) -> MagicMock:
    prop = MagicMock()
    prop.title = title
    return prop


def _make_full_detail_dict(title: str) -> dict:
    return {
        "id": 60,
        "title": title,
        "city": "Asuncion",
        "operation": "venta",
    }


# ---------------------------------------------------------------------------
# TestBuildOriginContextVistaPublica
# ---------------------------------------------------------------------------


class TestBuildOriginContextVistaPublica:
    """build_origin_context: numeric prop codes via properties table fallback."""

    @pytest.mark.asyncio
    async def test_numeric_ref_ic_miss_property_exists_returns_title_note(self):
        """ref='00060', IC miss, property id=60 with title → DIRECTO note with title.

        RED: This test exercises the new get_full_detail fallback branch in
        build_origin_context, which does not yet exist.
        """
        session = _mock_session_with_no_preferences()
        contact = _make_contact(infocasas_ref="00060")

        ic_miss = None  # InfoCasas has no record for this numeric ref
        property_title = "Apartamento en Asuncion Centro"
        full_detail = _make_full_detail_dict(title=property_title)

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=ic_miss),
        ), patch(
            "app.repositories.property_repo.PropertyRepository.get_full_detail",
            new=AsyncMock(return_value=full_detail),
        ) as mock_get_full:
            cm = ConversationManager()
            note = await cm.build_origin_context(session, contact, mode="recepcionista")

        # Must include the resolved title.
        assert property_title in note, (
            f"Note must contain the resolved title {property_title!r}. Got: {note!r}"
        )
        # Must include the original code unchanged.
        assert "CÓDIGO 00060" in note, (
            f"Note must reference CÓDIGO 00060. Got: {note!r}"
        )
        # Must be the DIRECTO-with-title note shape.
        assert "Origen del lead (DIRECTO)" in note, (
            f"Note must start with DIRECTO shape. Got: {note!r}"
        )
        # get_full_detail must have been called with int(60).
        mock_get_full.assert_awaited_once_with(session, 60)

    @pytest.mark.asyncio
    async def test_numeric_ref_ic_miss_property_also_absent_returns_code_only(self):
        """ref numeric + IC miss + no property row → CÓDIGO-only note (unchanged)."""
        session = _mock_session_with_no_preferences()
        contact = _make_contact(infocasas_ref="00060")

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.repositories.property_repo.PropertyRepository.get_full_detail",
            new=AsyncMock(return_value=None),
        ):
            cm = ConversationManager()
            note = await cm.build_origin_context(session, contact, mode="recepcionista")

        # Must be the CÓDIGO-only shape (title absent).
        assert "CÓDIGO 00060" in note, (
            f"Note must reference CÓDIGO 00060. Got: {note!r}"
        )
        assert "Origen del lead (DIRECTO)" in note, (
            f"Note must use DIRECTO shape. Got: {note!r}"
        )
        # Must NOT contain any title tokens (it is just CÓDIGO-only).
        assert "TÍTULO" not in note or "propiedad" in note.lower(), (
            "CÓDIGO-only note must not embed a resolved title."
        )
        # The note must NOT contain the word "propiedad \"" (title-note pattern).
        assert 'propiedad "' not in note, (
            f"CÓDIGO-only note must not contain quoted title. Got: {note!r}"
        )

    @pytest.mark.asyncio
    async def test_alphanumeric_ic_ref_uses_ic_path_not_full_detail(self):
        """ref='EC1754' (alphanumeric) + IC hit → IC title surfaced; get_full_detail NOT called.

        Regression guard: the new properties-table fallback must not affect the
        existing IC lookup path.
        """
        session = _mock_session_with_no_preferences()
        contact = _make_contact(infocasas_ref="EC1754", source="infocasas")

        ic_title = "Casa en Fernando de la Mora"
        ic_prop = _mock_ic_prop(title=ic_title)

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=ic_prop),
        ), patch(
            "app.repositories.property_repo.PropertyRepository.get_full_detail",
            new=AsyncMock(return_value=None),
        ) as mock_get_full:
            cm = ConversationManager()
            note = await cm.build_origin_context(session, contact, mode="recepcionista")

        # IC title must be in the note.
        assert ic_title in note, (
            f"IC title {ic_title!r} must appear in note. Got: {note!r}"
        )
        assert "CÓDIGO EC1754" in note, (
            f"Note must reference CÓDIGO EC1754. Got: {note!r}"
        )
        # get_full_detail must NOT have been called (IC path resolved title).
        mock_get_full.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_full_detail_exception_falls_to_code_only_no_crash(self):
        """Exception in get_full_detail → defensive: no crash, returns CÓDIGO-only note."""
        session = _mock_session_with_no_preferences()
        contact = _make_contact(infocasas_ref="00060")

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.repositories.property_repo.PropertyRepository.get_full_detail",
            new=AsyncMock(side_effect=RuntimeError("DB timeout")),
        ):
            cm = ConversationManager()
            # Must not raise.
            note = await cm.build_origin_context(session, contact, mode="recepcionista")

        # Fell back to CÓDIGO-only.
        assert "CÓDIGO 00060" in note, (
            f"After exception, note must fall back to CÓDIGO-only. Got: {note!r}"
        )
        assert "Origen del lead (DIRECTO)" in note


# ---------------------------------------------------------------------------
# TestDetectorNumericCodes  (E2E-lite / consistency)
# ---------------------------------------------------------------------------


class TestDetectorNumericCodes:
    """Detector consistency for numeric-id prop codes (M6.4 CTA shape).

    These are pure-function unit tests — no DB, no mocks.
    """

    def test_handshake_detected_numeric_5_digits(self):
        """CTA with zero-padded 5-digit id '00060' is a vista_publica handshake."""
        text = "Hola! Me interesa la propiedad 00060 que vi en onnix.com.py"
        assert _is_vista_publica_handshake(text), (
            f"Numeric 5-digit code CTA must be detected as handshake: {text!r}"
        )

    def test_prop_code_extracted_numeric_5_digits(self):
        """_extract_prop_code returns '00060' from the 5-digit CTA."""
        text = "Hola! Me interesa la propiedad 00060 que vi en onnix.com.py"
        assert _extract_prop_code(text) == "00060", (
            f"Expected '00060', got {_extract_prop_code(text)!r}"
        )

    def test_handshake_detected_numeric_7_digits(self):
        """CTA with 7-digit id '1977528' is a vista_publica handshake."""
        text = "Hola! Me interesa la propiedad 1977528 que vi en onnix.com.py"
        assert _is_vista_publica_handshake(text), (
            f"Numeric 7-digit code CTA must be detected as handshake: {text!r}"
        )

    def test_prop_code_extracted_numeric_7_digits(self):
        """_extract_prop_code returns '1977528' from the 7-digit CTA."""
        text = "Hola! Me interesa la propiedad 1977528 que vi en onnix.com.py"
        assert _extract_prop_code(text) == "1977528", (
            f"Expected '1977528', got {_extract_prop_code(text)!r}"
        )

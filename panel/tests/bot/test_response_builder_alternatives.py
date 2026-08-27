"""Tests for ResponseBuilder alternative buttons (Fase F).

Covers:
7. BotResponse with metadata["alternatives"] → payload has buttons.
8. alt.label > 20 chars → button label truncated to <= 20 chars.
9. Button payload matches alt["callback_payload"].
"""
from __future__ import annotations

import sys
from pathlib import Path

_panel_dir = str(Path(__file__).resolve().parent.parent)
if _panel_dir not in sys.path:
    sys.path.insert(0, _panel_dir)

from app.bot.core.response_builder import ResponseBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_alt(
    label: str = "En Lambare hay 8 deptos",
    payload: str = "ALT:zona_vecina:lambare",
) -> dict:
    return {
        "id": "zona_vecina:lambare",
        "label": label,
        "count": 8,
        "filters": {"ciudad": "lambare"},
        "reason": "zona vecina",
        "callback_payload": payload,
    }


# ---------------------------------------------------------------------------
# Test 7: payload has buttons when metadata["alternatives"] is present
# ---------------------------------------------------------------------------

class TestResponseWithAlternativesHasButtons:
    def test_response_with_alternatives_has_buttons(self):
        """BotResponse with 3 alternatives → payload message has 3 buttons."""
        builder = ResponseBuilder()
        alts = [
            _make_alt("En Lambare hay 8 deptos", "ALT:zona_vecina:lambare"),
            _make_alt("En San Lorenzo hay 5", "ALT:zona_vecina:san_lorenzo"),
            _make_alt("Hasta 130k hay 4 deptos", "ALT:presupuesto_20pct"),
        ]
        metadata = {"alternatives": alts}

        payload = builder.build_payload(
            text="No encontré departamentos en Villa Morra a ese precio.",
            intent="conversacion",
            properties=[],
            channel="whatsapp",
            metadata=metadata,
        )

        assert payload.messages, "Payload must have messages"
        # The first (and only) message should have the alternative buttons
        msg = payload.messages[0]
        assert len(msg.buttons) == 3, f"Expected 3 buttons, got {len(msg.buttons)}"

    def test_response_without_alternatives_has_no_buttons(self):
        """No metadata → payload message has no buttons."""
        builder = ResponseBuilder()

        payload = builder.build_payload(
            text="No encontré nada.",
            intent="conversacion",
            properties=[],
            channel="whatsapp",
        )

        assert payload.messages
        msg = payload.messages[0]
        assert msg.buttons == []


# ---------------------------------------------------------------------------
# Test 8: label truncated to <= 20 chars
# ---------------------------------------------------------------------------

class TestButtonLabelTruncated:
    def test_button_label_truncated_to_20_chars(self):
        """alt.label of 50 chars → button label has <= 20 chars."""
        builder = ResponseBuilder()
        long_label = "A" * 50  # 50 chars, well above limit
        alt = _make_alt(label=long_label, payload="ALT:too_long_label")
        metadata = {"alternatives": [alt]}

        payload = builder.build_payload(
            text="Sin resultados.",
            intent="conversacion",
            properties=[],
            channel="whatsapp",
            metadata=metadata,
        )

        assert payload.messages
        buttons = payload.messages[0].buttons
        assert len(buttons) == 1
        assert len(buttons[0]["text"]) <= 20

    def test_button_label_within_limit_not_truncated(self):
        """alt.label of exactly 20 chars → no truncation (no ellipsis)."""
        builder = ResponseBuilder()
        exact_label = "B" * 20  # exactly at limit
        alt = _make_alt(label=exact_label, payload="ALT:exact_20")
        metadata = {"alternatives": [alt]}

        payload = builder.build_payload(
            text="Sin resultados.",
            intent="conversacion",
            properties=[],
            channel="whatsapp",
            metadata=metadata,
        )

        assert payload.messages
        buttons = payload.messages[0].buttons
        assert buttons[0]["text"] == exact_label  # unchanged

    def test_button_label_truncated_to_19_plus_ellipsis(self):
        """Label of 30 chars → truncated to 19 chars + ellipsis (20 total)."""
        builder = ResponseBuilder()
        label = "C" * 30
        alt = _make_alt(label=label, payload="ALT:thirty_chars")
        metadata = {"alternatives": [alt]}

        payload = builder.build_payload(
            text="Sin resultados.",
            intent="conversacion",
            properties=[],
            channel="whatsapp",
            metadata=metadata,
        )

        buttons = payload.messages[0].buttons
        # ellipsis char is a single unicode char (1 char), so 19 + 1 = 20
        assert len(buttons[0]["text"]) == 20
        assert buttons[0]["text"].endswith("\u2026")


# ---------------------------------------------------------------------------
# Test 9: button payload matches alt["callback_payload"]
# ---------------------------------------------------------------------------

class TestButtonPayloadMatchesAlt:
    def test_button_payload_uses_callback_payload_from_alt(self):
        """callback_data of button equals alt['callback_payload']."""
        builder = ResponseBuilder()
        expected_payload = "ALT:zona_vecina:luque"
        alt = _make_alt(label="En Luque hay 6", payload=expected_payload)
        metadata = {"alternatives": [alt]}

        payload = builder.build_payload(
            text="No encontré nada exacto.",
            intent="conversacion",
            properties=[],
            channel="telegram",
            metadata=metadata,
        )

        assert payload.messages
        buttons = payload.messages[0].buttons
        assert len(buttons) == 1
        assert buttons[0]["callback_data"] == expected_payload

    def test_button_payload_max_3_alternatives(self):
        """More than 3 alternatives → only 3 buttons emitted."""
        builder = ResponseBuilder()
        alts = [
            _make_alt(f"Opcion {i}", f"ALT:alt_{i}")
            for i in range(5)
        ]
        metadata = {"alternatives": alts}

        payload = builder.build_payload(
            text="Sin resultados.",
            intent="conversacion",
            properties=[],
            channel="telegram",
            metadata=metadata,
        )

        buttons = payload.messages[0].buttons
        assert len(buttons) == 3  # capped at 3

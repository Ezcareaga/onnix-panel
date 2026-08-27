"""Tests for ResponseBuilder — property cards, buttons, photo URLs, channel formatting.

Plan 62-03: CORE-03 ResponseBuilder tests.
"""
from __future__ import annotations

import pytest

from app.bot.core.response_builder import (
    ResponseBuilder,
    TelegramFormatter,
    WhatsAppFormatter,
    build_photo_urls,
    TEXT_ONLY_INTENTS,
    _guide_block,
)
from app.bot.ai.prompts import DEFAULT_OPT_OUT_TEXT, get_response_template
from app.bot.core.types import ChannelPayload, PayloadMessage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PROPERTY = {
    "id": 12345,
    "source": "onnix",
    "external_id": "PY-ASU-001",
    "title": "Casa en Villa Morra",
    "description": "Hermosa casa de 3 dormitorios",
    "price_usd": 180000.0,
    "price_pyg": None,
    "city": "Asuncion",
    "neighborhood": "Villa Morra",
    "operation": "venta",
    "property_type": "casa",
    "bedrooms": 3,
    "bathrooms": 2,
    "total_area_m2": 250.0,
    "built_area_m2": 180.0,
    "main_image_url": "https://example.com/img.jpg",
    "image_urls": [
        "https://cdn.example.com/img1.jpg",
        "https://cdn.example.com/img2.jpg",
        "https://cdn.example.com/img3.jpg",
        "https://cdn.example.com/img4.jpg",
        "https://cdn.example.com/img5.jpg",
    ],
    "local_image_count": 5,
    "address": "Av. Mariscal Lopez 1234",
}

SAMPLE_PROPERTY_2 = {
    "id": 12346,
    "source": "remax",
    "external_id": "RX-001",
    "title": "Departamento en Carmelitas",
    "price_usd": 95000.0,
    "price_pyg": None,
    "city": "Asuncion",
    "neighborhood": "Las Lomas",
    "operation": "venta",
    "property_type": "departamento",
    "bedrooms": 2,
    "bathrooms": 1,
    "total_area_m2": 85.0,
    "built_area_m2": 75.0,
    "main_image_url": "https://cdn.remax.com/rx001.jpg",
    "image_urls": ["https://cdn.remax.com/rx001.jpg", "https://cdn.remax.com/rx002.jpg"],
    "local_image_count": 3,
}


# ---------------------------------------------------------------------------
# TestPropertyCard
# ---------------------------------------------------------------------------


class TestPropertyCard:
    """Tests for build_property_card formatting."""

    def setup_method(self):
        self.builder = ResponseBuilder()

    def test_card_contains_location(self):
        card = self.builder.build_property_card(SAMPLE_PROPERTY, channel="telegram")
        assert "Asuncion" in card
        assert "Villa Morra" in card

    def test_card_contains_price_usd(self):
        card = self.builder.build_property_card(SAMPLE_PROPERTY, channel="telegram")
        # Accept locale-appropriate formatting: "180.000" or "180,000"
        assert "USD" in card
        assert "180" in card

    def test_card_contains_specs(self):
        card = self.builder.build_property_card(SAMPLE_PROPERTY, channel="telegram")
        assert "3" in card  # bedrooms
        assert "250" in card  # total area

    def test_card_contains_operation(self):
        card = self.builder.build_property_card(SAMPLE_PROPERTY, channel="telegram")
        assert "Venta" in card or "venta" in card.lower()

    def test_card_telegram_html(self):
        tg_card = self.builder.build_property_card(SAMPLE_PROPERTY, channel="telegram")
        wa_card = self.builder.build_property_card(SAMPLE_PROPERTY, channel="whatsapp")
        # Telegram uses HTML bold tags
        assert "<b>" in tg_card
        # WhatsApp uses * for bold
        assert "*" in wa_card
        assert "<b>" not in wa_card


# ---------------------------------------------------------------------------
# TestPhotoUrls
# ---------------------------------------------------------------------------


class TestPhotoUrls:
    """Tests for build_photo_urls generation."""

    def setup_method(self):
        self.builder = ResponseBuilder()

    def test_photo_url_prefers_image_urls_cdn(self):
        """When image_urls exists, use CDN URLs directly."""
        urls = self.builder.build_photo_urls(SAMPLE_PROPERTY)
        assert urls == ["https://cdn.example.com/img1.jpg"]

    def test_photo_url_multiple_cdn(self):
        """image_urls returns multiple CDN URLs up to max_photos."""
        urls = self.builder.build_photo_urls(SAMPLE_PROPERTY, max_photos=5)
        assert len(urls) == 5
        assert all("cdn.example.com" in u for u in urls)

    def test_photo_url_fallback_main_image_url(self):
        """Without image_urls, fall back to main_image_url."""
        prop = {**SAMPLE_PROPERTY, "image_urls": None}
        urls = self.builder.build_photo_urls(prop)
        assert urls == ["https://example.com/img.jpg"]

    def test_photo_url_empty_image_urls_fallback(self):
        """Empty image_urls list falls back to main_image_url."""
        prop = {**SAMPLE_PROPERTY, "image_urls": []}
        urls = self.builder.build_photo_urls(prop)
        assert urls == ["https://example.com/img.jpg"]

    def test_photo_url_no_urls_at_all(self):
        prop = {**SAMPLE_PROPERTY, "image_urls": None, "main_image_url": None}
        urls = self.builder.build_photo_urls(prop)
        assert urls == []

    def test_photo_url_deduplication(self):
        """Duplicate URLs in image_urls are removed."""
        prop = {**SAMPLE_PROPERTY, "image_urls": ["https://a.jpg", "https://a.jpg", "https://b.jpg"]}
        urls = self.builder.build_photo_urls(prop, max_photos=5)
        assert urls == ["https://a.jpg", "https://b.jpg"]


# ---------------------------------------------------------------------------
# TestButtons
# ---------------------------------------------------------------------------


class TestButtons:
    """Tests for build_buttons per intent."""

    def setup_method(self):
        self.builder = ResponseBuilder()

    def test_busqueda_buttons(self):
        buttons = self.builder.build_buttons(
            intent="busqueda",
            properties=[SAMPLE_PROPERTY, SAMPLE_PROPERTY_2],
            has_pending=True,
        )
        callback_datas = [b.get("callback_data", "") for b in buttons]
        assert "detail_1" in callback_datas
        assert "detail_2" in callback_datas
        # Buttons show property id + neighborhood
        texts = [b.get("text", "") for b in buttons]
        assert any("12345" in t for t in texts)
        assert any("Villa Morra" in t for t in texts)
        # "Mas opciones" button when has_pending=True
        assert any("opciones" in t.lower() for t in texts)

    def test_busqueda_buttons_no_asesor(self):
        """busqueda buttons should NOT include hablar_asesor (text hint instead)."""
        buttons = self.builder.build_buttons(
            intent="busqueda",
            properties=[SAMPLE_PROPERTY, SAMPLE_PROPERTY_2],
            has_pending=True,
        )
        callback_datas = [b.get("callback_data", "") for b in buttons]
        assert "hablar_asesor" not in callback_datas

    def test_busqueda_buttons_no_pending(self):
        buttons = self.builder.build_buttons(
            intent="busqueda",
            properties=[SAMPLE_PROPERTY, SAMPLE_PROPERTY_2],
            has_pending=False,
        )
        texts = [b.get("text", "") for b in buttons]
        # No "Mas opciones" button
        assert not any("opciones" in t.lower() for t in texts)

    def test_detalle_buttons(self):
        buttons = self.builder.build_buttons(
            intent="detalle",
            properties=[SAMPLE_PROPERTY],
        )
        # v7 remodel: detalle has no fixed buttons — Claude guides via body text.
        # Legacy hablar_asesor / ver_mas / seguir_buscando buttons were dropped
        # together with wa_tpl_detalle.
        assert buttons == []

    def test_detalle_buttons_with_pending(self):
        buttons = self.builder.build_buttons(
            intent="detalle",
            properties=[SAMPLE_PROPERTY],
            has_pending=True,
        )
        # Same as test_detalle_buttons — pending pagination no longer adds a
        # ver_mas button on detalle intent; user asks for more via text.
        assert buttons == []

    def test_saludo_buttons(self):
        # B2: saludo is now plain text — no buttons
        buttons = self.builder.build_buttons(intent="saludo")
        assert isinstance(buttons, list)
        assert buttons == []

    def test_lead_buttons(self):
        buttons = self.builder.build_buttons(intent="lead")
        # Lead intent: empty or confirmation only
        assert isinstance(buttons, list)
        callback_datas = [b.get("callback_data", "") for b in buttons]
        assert "detail_1" not in callback_datas

    def test_paginacion_buttons(self):
        buttons = self.builder.build_buttons(
            intent="paginacion",
            properties=[SAMPLE_PROPERTY, SAMPLE_PROPERTY_2],
            has_pending=True,
        )
        callback_datas = [b.get("callback_data", "") for b in buttons]
        assert "detail_1" in callback_datas
        assert "detail_2" in callback_datas

    def test_busqueda_incompleta_operacion_buttons(self):
        """busqueda_incompleta_operacion has Comprar and Alquilar buttons."""
        buttons = self.builder.build_buttons(intent="busqueda_incompleta_operacion")
        assert len(buttons) == 2
        callback_datas = [b.get("callback_data", "") for b in buttons]
        assert "SEARCH_COMPRA" in callback_datas
        assert "SEARCH_ALQUILER" in callback_datas

    def test_busqueda_incompleta_zona_buttons_empty(self):
        """busqueda_incompleta_zona returns empty (WA template handles buttons)."""
        buttons = self.builder.build_buttons(intent="busqueda_incompleta_zona")
        assert buttons == []


# ---------------------------------------------------------------------------
# TestTemplateKeys
# ---------------------------------------------------------------------------


class TestTemplateKeys:
    """Tests for WhatsAppFormatter.get_template_key."""

    def setup_method(self):
        self.fmt = WhatsAppFormatter()

    def test_operacion_template_no_wizard(self):
        """busqueda_incompleta_operacion no longer returns a wizard template key."""
        assert self.fmt.get_template_key("busqueda_incompleta_operacion") is None

    def test_zona_template_no_wizard(self):
        """busqueda_incompleta_zona no longer returns a wizard template key."""
        assert self.fmt.get_template_key("busqueda_incompleta_zona") is None

    def test_elegir_zona_no_wizard(self):
        """elegir_zona no longer returns a wizard template key."""
        assert self.fmt.get_template_key("elegir_zona") is None

    def test_busqueda_incompleta_no_wizard(self):
        """busqueda_incompleta no longer returns a wizard template key."""
        assert self.fmt.get_template_key("busqueda_incompleta") is None

    def test_paginacion_template(self):
        # v7 remodel: paginacion without results falls back to plain text
        # (no more wa_tpl_paginacion). Multi-result pagination still uses
        # wa_tpl_res2/wa_tpl_res2_con_pendientes — tested separately.
        assert self.fmt.get_template_key("paginacion") is None


# ---------------------------------------------------------------------------
# TestTelegramFormatter
# ---------------------------------------------------------------------------


class TestTelegramFormatter:
    """Tests for TelegramFormatter-specific behaviour."""

    def setup_method(self):
        self.fmt = TelegramFormatter()

    def test_escape_html(self):
        result = TelegramFormatter.escape_html("A & B < C > D")
        assert result == "A &amp; B &lt; C &gt; D"

    def test_format_card_html(self):
        card = self.fmt.format_property_card(SAMPLE_PROPERTY)
        assert "<b>" in card
        # Title should be in the card
        assert "Casa en Villa Morra" in card or "Casa en Villa Morra" in card.replace("&amp;", "&")

    def test_inline_keyboard_structure(self):
        buttons = [
            {"text": "Ver detalles", "callback_data": "detail_1"},
            {"text": "Ver mas", "callback_data": "ver_mas"},
        ]
        keyboard = self.fmt.build_inline_keyboard(buttons)
        assert isinstance(keyboard, list)
        for btn in keyboard:
            assert "text" in btn
            assert "callback_data" in btn

    def test_caption_max_length(self):
        long_text = "A" * 2000
        result = self.fmt.format_caption(long_text)
        assert len(result) <= 1024
        assert result.endswith("...")

    def test_media_group_urls(self):
        # With image_urls set, returns CDN URLs
        urls = build_photo_urls(SAMPLE_PROPERTY, max_photos=5)
        assert len(urls) == 5
        assert all("cdn.example.com" in url for url in urls)
        # Without image_urls, falls back to main_image_url
        prop_no_cdn = {**SAMPLE_PROPERTY, "image_urls": None}
        fallback_urls = build_photo_urls(prop_no_cdn)
        assert fallback_urls == ["https://example.com/img.jpg"]


# ---------------------------------------------------------------------------
# TestWhatsAppFormatter
# ---------------------------------------------------------------------------


class TestWhatsAppFormatter:
    """Tests for WhatsAppFormatter-specific behaviour."""

    def setup_method(self):
        self.fmt = WhatsAppFormatter()

    def test_html_to_wa(self):
        result = WhatsAppFormatter.html_to_wa("<b>bold</b> and <i>italic</i>")
        assert result == "*bold* and _italic_"

    def test_br_to_newline(self):
        result = WhatsAppFormatter.html_to_wa("line1<br>line2")
        assert result == "line1\nline2"
        # Also test self-closing br
        result2 = WhatsAppFormatter.html_to_wa("line1<br/>line2")
        assert result2 == "line1\nline2"

    def test_strip_unsupported_tags(self):
        result = WhatsAppFormatter.html_to_wa("<a href='x'>link</a>")
        assert result == "link"

    def test_wa_body_max_length(self):
        long_text = "B" * 2000
        result = self.fmt.format_body(long_text)
        assert len(result) <= 1600
        assert result.endswith("...")

    def test_wa_template_key(self):
        # B2: saludo no longer uses a WA template
        assert self.fmt.get_template_key("saludo") is None
        assert self.fmt.get_template_key("busqueda", num_results=2) == "wa_tpl_res2"


# ---------------------------------------------------------------------------
# TestBuildPayload
# ---------------------------------------------------------------------------


class TestBuildPayload:
    """Tests for build_payload assembling ChannelPayload."""

    def setup_method(self):
        self.builder = ResponseBuilder()

    def test_payload_text_only_telegram(self):
        payload = self.builder.build_payload(
            text="Hola!",
            intent="saludo",
            properties=[],
            channel="telegram",
        )
        assert isinstance(payload, ChannelPayload)
        assert len(payload.messages) >= 1
        assert "Hola" in payload.messages[0].text

    def test_payload_busqueda_telegram(self):
        payload = self.builder.build_payload(
            text="Encontre estas propiedades:",
            intent="busqueda",
            properties=[SAMPLE_PROPERTY, SAMPLE_PROPERTY_2],
            channel="telegram",
        )
        assert isinstance(payload, ChannelPayload)
        # intro + 2 property cards + guide text = at least 4 messages
        assert len(payload.messages) >= 3
        # Second-to-last message (last property card) should have buttons;
        # last message is the guide text (no buttons).
        button_msgs = [m for m in payload.messages if m.buttons]
        assert len(button_msgs) > 0

    def test_payload_busqueda_whatsapp(self):
        payload = self.builder.build_payload(
            text="Encontre estas propiedades:",
            intent="busqueda",
            properties=[SAMPLE_PROPERTY, SAMPLE_PROPERTY_2],
            channel="whatsapp",
            has_pending=True,
        )
        assert isinstance(payload, ChannelPayload)
        # Structure: intro text + 2 photo cards + button template = 4 messages
        assert len(payload.messages) == 4
        # WA messages use WA formatting (no HTML tags)
        for msg in payload.messages:
            if msg.text:
                assert "<b>" not in msg.text
        # First message is intro text (no photo, no template)
        assert payload.messages[0].photo_url is None
        assert payload.messages[0].template_id is None
        # Property cards have photos, no template
        assert payload.messages[1].photo_url is not None
        assert payload.messages[1].template_id is None
        assert payload.messages[2].photo_url is not None
        assert payload.messages[2].template_id is None
        # Last message is button template with summary
        last_msg = payload.messages[-1]
        assert last_msg.template_id is not None
        assert "12345" in last_msg.text or "12346" in last_msg.text
        assert "opciones" in last_msg.text.lower()

    def test_payload_detalle_with_gallery(self):
        payload = self.builder.build_payload(
            text="Detalles de la propiedad:",
            intent="detalle",
            properties=[SAMPLE_PROPERTY],
            channel="telegram",
        )
        assert isinstance(payload, ChannelPayload)
        # Detail intent should produce multiple messages with photo URLs (gallery)
        photos = [m for m in payload.messages if m.photo_url is not None]
        assert len(photos) > 0
        # v7 remodel: no fixed buttons on detalle — Claude guides in body text.
        has_buttons = any(len(m.buttons) > 0 for m in payload.messages)
        assert not has_buttons

    def test_payload_empty_properties(self):
        payload = self.builder.build_payload(
            text="No encontre propiedades con esos filtros.",
            intent="busqueda",
            properties=[],
            channel="telegram",
        )
        assert isinstance(payload, ChannelPayload)
        # Single text message (no results)
        assert len(payload.messages) >= 1
        assert "No encontre" in payload.messages[0].text or len(payload.messages) == 1

# ---------------------------------------------------------------------------
# TestGoogleMapsLink
# ---------------------------------------------------------------------------


class TestGoogleMapsLink:
    """Tests for Google Maps link in property detail."""

    def setup_method(self):
        self.builder = ResponseBuilder()

    def _get_detail_caption(self, prop: dict, channel: str = "telegram") -> str:
        """Helper: build detail payload and extract the caption text."""
        payload = self.builder.build_payload(
            text="Detalles:",
            intent="detalle",
            properties=[prop],
            channel=channel,
        )
        # The caption is in the last photo message (or last non-template message)
        for msg in reversed(payload.messages):
            if msg.photo_url or (msg.text and "maps.google.com" in msg.text):
                return msg.text
            # For telegram, caption is on the last photo message
            if msg.text and len(msg.text) > 50:
                return msg.text
        return ""

    def test_maps_link_present_when_coords_available_telegram(self):
        prop = {**SAMPLE_PROPERTY, "latitude": -25.2867, "longitude": -57.5803}
        caption = self._get_detail_caption(prop, channel="telegram")
        assert "maps.google.com/?q=-25.2867,-57.5803" in caption

    def test_maps_link_present_when_coords_available_whatsapp(self):
        prop = {**SAMPLE_PROPERTY, "latitude": -25.2867, "longitude": -57.5803}
        caption = self._get_detail_caption(prop, channel="whatsapp")
        assert "maps.google.com/?q=-25.2867,-57.5803" in caption

    def test_maps_link_absent_when_no_coords(self):
        prop = {**SAMPLE_PROPERTY, "latitude": None, "longitude": None}
        caption = self._get_detail_caption(prop, channel="telegram")
        assert "maps.google.com" not in caption

    def test_maps_link_absent_when_only_lat(self):
        prop = {**SAMPLE_PROPERTY, "latitude": -25.2867, "longitude": None}
        caption = self._get_detail_caption(prop, channel="telegram")
        assert "maps.google.com" not in caption

    def test_maps_link_absent_when_only_lng(self):
        prop = {**SAMPLE_PROPERTY, "latitude": None, "longitude": -57.5803}
        caption = self._get_detail_caption(prop, channel="telegram")
        assert "maps.google.com" not in caption

    def test_maps_link_absent_when_coords_missing_entirely(self):
        """Property dict without latitude/longitude keys at all."""
        prop = {k: v for k, v in SAMPLE_PROPERTY.items() if k not in ("latitude", "longitude")}
        caption = self._get_detail_caption(prop, channel="telegram")
        assert "maps.google.com" not in caption


# ---------------------------------------------------------------------------
# TestDynamicWATemplateSelection
# ---------------------------------------------------------------------------


class TestDynamicWATemplateSelection:
    """Tests for dynamic WA template selection based on result count and pending status."""

    def setup_method(self):
        self.fmt = WhatsAppFormatter()
        self.builder = ResponseBuilder()

    def test_get_template_key_busqueda_2_with_pending(self):
        """2 results + pending -> con_pendientes template."""
        key = self.fmt.get_template_key("busqueda", num_results=2, has_pending=True)
        assert key == "wa_tpl_res2_con_pendientes"

    def test_get_template_key_busqueda_2_no_pending(self):
        """2 results + no pending -> standard res2 template."""
        key = self.fmt.get_template_key("busqueda", num_results=2, has_pending=False)
        assert key == "wa_tpl_res2"

    def test_get_template_key_busqueda_1_result(self):
        """1 result -> plain text (no template). v7 remodel dropped wa_tpl_res1_con_asesor."""
        assert self.fmt.get_template_key("busqueda", num_results=1, has_pending=True) is None
        assert self.fmt.get_template_key("busqueda", num_results=1, has_pending=False) is None

    def test_get_template_key_paginacion_with_pending(self):
        """paginacion with 2 results + pending -> con_pendientes template."""
        key = self.fmt.get_template_key("paginacion", num_results=2, has_pending=True)
        assert key == "wa_tpl_res2_con_pendientes"

    def test_get_template_key_paginacion_no_pending(self):
        """paginacion with 2 results + no pending -> standard res2 template."""
        key = self.fmt.get_template_key("paginacion", num_results=2, has_pending=False)
        assert key == "wa_tpl_res2"

    def test_get_template_key_paginacion_1_result(self):
        """paginacion with 1 result -> plain text (no template)."""
        assert self.fmt.get_template_key("paginacion", num_results=1, has_pending=False) is None

    def test_get_template_key_paginacion_no_results(self):
        """paginacion with 0 results -> plain text (no template). Dropped wa_tpl_paginacion."""
        assert self.fmt.get_template_key("paginacion", num_results=0) is None

    def test_wa_button_summary_with_pending(self):
        """Button summary with pending results mentions 'Mas opciones' button, not 'Escribi'."""
        summary = self.builder._build_wa_button_summary(
            [SAMPLE_PROPERTY, SAMPLE_PROPERTY_2], has_pending=True,
        )
        assert "M\u00e1s opciones" in summary
        assert "Toc\u00e1" in summary
        # Should NOT say "Escribi mas opciones"
        assert "Escrib\u00ed *m\u00e1s opciones*" not in summary

    def test_wa_button_summary_no_pending(self):
        """Button summary without pending results does NOT mention 'mas opciones'."""
        summary = self.builder._build_wa_button_summary(
            [SAMPLE_PROPERTY, SAMPLE_PROPERTY_2], has_pending=False,
        )
        assert "m\u00e1s opciones" not in summary.lower()
        assert "M\u00e1s opciones" not in summary

    def test_build_payload_passes_has_pending_to_template(self):
        """WA busqueda payload with has_pending=True uses con_pendientes template."""
        payload = self.builder.build_payload(
            text="Resultados:",
            intent="busqueda",
            properties=[SAMPLE_PROPERTY, SAMPLE_PROPERTY_2],
            channel="whatsapp",
            has_pending=True,
        )
        # The last message should be the template message
        template_msg = payload.messages[-1]
        assert template_msg.template_id == "wa_tpl_res2_con_pendientes"

    def test_build_payload_no_pending_uses_res2(self):
        """WA busqueda payload with has_pending=False uses standard res2 template."""
        payload = self.builder.build_payload(
            text="Resultados:",
            intent="busqueda",
            properties=[SAMPLE_PROPERTY, SAMPLE_PROPERTY_2],
            channel="whatsapp",
            has_pending=False,
        )
        template_msg = payload.messages[-1]
        assert template_msg.template_id == "wa_tpl_res2"

    def test_build_payload_1_result_uses_no_template(self):
        """WA busqueda with 1 result is plain text — no template (v7 remodel)."""
        payload = self.builder.build_payload(
            text="Resultado:",
            intent="busqueda",
            properties=[SAMPLE_PROPERTY],
            channel="whatsapp",
            has_pending=False,
        )
        # No message should carry a template_id.
        assert all(m.template_id is None for m in payload.messages)


# ---------------------------------------------------------------------------
# TestGuideBlock
# ---------------------------------------------------------------------------


class TestGuideBlock:
    """Tests for the _guide_block() helper function."""

    def test_guide_block_busqueda(self):
        """busqueda intent returns adjustment guidance."""
        block = _guide_block("busqueda")
        assert "ajustar" in block.lower() or "zona" in block.lower() or "precio" in block.lower()
        assert block.startswith("\n\n")

    def test_guide_block_paginacion(self):
        """paginacion returns same guidance as busqueda."""
        block = _guide_block("paginacion")
        assert block == _guide_block("busqueda")

    def test_guide_block_detalle(self):
        """detalle intent returns follow-up guidance."""
        block = _guide_block("detalle")
        assert "asesor" in block.lower()
        assert "tipo" in block.lower() or "zona" in block.lower()
        assert block.startswith("\n\n")

    def test_guide_block_conversacion_empty(self):
        """conversacion returns empty string (no guidance needed)."""
        block = _guide_block("conversacion")
        assert block == ""

    def test_guide_block_lead_empty(self):
        """lead intent returns empty string."""
        block = _guide_block("lead")
        assert block == ""

    def test_guide_block_unknown_empty(self):
        """Unknown intent returns empty string."""
        block = _guide_block("unknown_intent")
        assert block == ""


# ---------------------------------------------------------------------------
# TestPlainTextConstants
# ---------------------------------------------------------------------------


class TestPlainTextTemplates:
    """Tests for plain text response templates (RESPONSE_TEMPLATES in prompts.py).

    NOTE: texto-specific assertions (identidad Onnix, no nombres propios, etc.)
    viven en tests/bot/test_prompts.py (M2.F7). Acá solo contratos estructurales.
    """

    def test_saludo_template_exists(self):
        text = get_response_template("saludo")
        assert isinstance(text, str)
        assert len(text) > 20
        assert "Onnix" in text or "Onnix" in text

    def test_lead_template_exists(self):
        text = get_response_template("lead")
        assert isinstance(text, str)
        assert len(text) > 10

    def test_busqueda_incompleta_template_exists(self):
        text = get_response_template("busqueda_incompleta")
        assert isinstance(text, str)
        assert len(text) > 10


# ---------------------------------------------------------------------------
# TestTextOnlyIntentsExtended
# ---------------------------------------------------------------------------


class TestTextOnlyIntentsExtended:
    """Tests that TEXT_ONLY_INTENTS includes all expected intents."""

    def test_wizard_intents_in_text_only(self):
        """Wizard intents must be text-only (no template)."""
        assert "busqueda_incompleta_operacion" in TEXT_ONLY_INTENTS
        assert "busqueda_incompleta_zona" in TEXT_ONLY_INTENTS
        assert "elegir_zona" in TEXT_ONLY_INTENTS

    def test_ic_intents_in_text_only(self):
        """IC intents must be in TEXT_ONLY_INTENTS."""
        assert "ic_welcome" in TEXT_ONLY_INTENTS
        assert "ic_reenviado_welcome" in TEXT_ONLY_INTENTS

    def test_manual_template_in_text_only(self):
        """manual_template must be in TEXT_ONLY_INTENTS."""
        assert "manual_template" in TEXT_ONLY_INTENTS

    def test_core_intents_still_present(self):
        """Verify original TEXT_ONLY_INTENTS members remain."""
        for intent in ("saludo", "conversacion", "lead", "opt_out", "busqueda_incompleta", "ambiguo_visita"):
            assert intent in TEXT_ONLY_INTENTS, f"{intent} missing from TEXT_ONLY_INTENTS"


# ---------------------------------------------------------------------------
# TestButtonSummaryWithPrice
# ---------------------------------------------------------------------------


class TestButtonSummaryWithPrice:
    """Tests for updated _build_wa_button_summary with price included."""

    def setup_method(self):
        self.builder = ResponseBuilder()

    def test_button_summary_includes_price(self):
        """Button summary should include formatted price for each property."""
        summary = self.builder._build_wa_button_summary(
            [SAMPLE_PROPERTY, SAMPLE_PROPERTY_2], has_pending=False,
        )
        # Price for SAMPLE_PROPERTY: USD 180.000
        assert "180" in summary
        # Price for SAMPLE_PROPERTY_2: USD 95.000
        assert "95" in summary

    def test_button_summary_no_hablar_asesor_line(self):
        """Button summary must NOT contain 'hablar con asesor' line (removed in refactor)."""
        summary = self.builder._build_wa_button_summary(
            [SAMPLE_PROPERTY, SAMPLE_PROPERTY_2], has_pending=False,
        )
        assert "hablar con asesor" not in summary.lower()

    def test_button_summary_with_pending_mentions_mas_opciones(self):
        """When has_pending, summary must mention 'Más opciones'."""
        summary = self.builder._build_wa_button_summary(
            [SAMPLE_PROPERTY, SAMPLE_PROPERTY_2], has_pending=True,
        )
        assert "Más opciones" in summary or "opciones" in summary.lower()

    def test_button_summary_no_pending_no_mas_opciones(self):
        """When not has_pending, summary must NOT mention 'más opciones'."""
        summary = self.builder._build_wa_button_summary(
            [SAMPLE_PROPERTY], has_pending=False,
        )
        assert "más opciones" not in summary.lower()
        assert "Más opciones" not in summary


# ---------------------------------------------------------------------------
# TestIntroTruncation
# ---------------------------------------------------------------------------


class TestIntroTruncation:
    """Test that intro text for busqueda is truncated at 150 chars."""

    def setup_method(self):
        self.builder = ResponseBuilder()

    def test_long_intro_truncated_at_150_telegram(self):
        """A >150-char intro text should be truncated to 150+ellipsis."""
        long_intro = "A" * 200
        payload = self.builder.build_payload(
            text=long_intro,
            intent="busqueda",
            properties=[SAMPLE_PROPERTY],
            channel="telegram",
        )
        # First message is the intro
        intro_text = payload.messages[0].text
        assert len(intro_text) <= 151 + 1  # 150 chars + ellipsis char
        assert intro_text.endswith("…")

    def test_short_intro_not_truncated(self):
        """A <=150-char intro should not be truncated."""
        short_intro = "Encontré estas propiedades para vos:"
        payload = self.builder.build_payload(
            text=short_intro,
            intent="busqueda",
            properties=[SAMPLE_PROPERTY],
            channel="telegram",
        )
        intro_text = payload.messages[0].text
        assert intro_text == short_intro

    def test_long_intro_not_truncated_when_relaxation_metadata_present(self):
        """Bug 2026-04-26: el aviso de relajación de filtros pasa por intro y se
        truncaba a 150 chars, dejando "Relajamos los criterios y ampliamos a…"
        sin contexto. Cuando metadata['relaxed_filters'] tiene contenido, el
        intro debe entregarse completo (el aviso ANTES de las propiedades
        necesita espacio para explicar qué se relajó).
        """
        long_intro = (
            "No encontré departamentos de 2 dormitorios en Villa Morra hasta "
            "4 millones Gs. Lo más cercano que encontré tiene 1 dormitorio y "
            "hasta 5.2 millones Gs en zonas vecinas como Vista Alegre y Luque. "
            "¿Querés ver esas opciones?"
        )
        assert len(long_intro) > 150  # sanity: el caso real supera el límite
        payload = self.builder.build_payload(
            text=long_intro,
            intent="busqueda",
            properties=[SAMPLE_PROPERTY],
            channel="telegram",
            metadata={"relaxed_filters": ["barrio Villa Morra eliminado"]},
        )
        intro_text = payload.messages[0].text
        assert intro_text == long_intro, (
            "Intro must NOT be truncated when relaxation message is present"
        )
        assert not intro_text.endswith("…"), (
            "Intro must not end with ellipsis when relaxation is active"
        )

    def test_long_intro_still_truncated_when_no_relaxation(self):
        """Regresión: sin relaxed_filters en metadata, el comportamiento
        original (truncar a 150 chars + '…') se preserva.
        """
        long_intro = "A" * 300
        # Empty metadata
        payload_empty = self.builder.build_payload(
            text=long_intro,
            intent="busqueda",
            properties=[SAMPLE_PROPERTY],
            channel="telegram",
            metadata={},
        )
        assert payload_empty.messages[0].text.endswith("…")
        assert len(payload_empty.messages[0].text) <= 152

        # relaxed_filters explicitly empty list
        payload_empty_list = self.builder.build_payload(
            text=long_intro,
            intent="busqueda",
            properties=[SAMPLE_PROPERTY],
            channel="telegram",
            metadata={"relaxed_filters": []},
        )
        assert payload_empty_list.messages[0].text.endswith("…")

    def test_long_intro_not_truncated_relaxation_whatsapp(self):
        """Mismo comportamiento en WhatsApp: el intro de relajación no se
        trunca en el mensaje separado anterior a las cards.
        """
        long_intro = (
            "No encontré departamentos de 2 dormitorios en Villa Morra hasta "
            "4 millones Gs. Lo más cercano que encontré tiene 1 dormitorio y "
            "hasta 5.2 millones Gs en zonas vecinas como Vista Alegre y Luque. "
            "¿Querés ver esas opciones?"
        )
        assert len(long_intro) > 150
        payload = self.builder.build_payload(
            text=long_intro,
            intent="busqueda",
            properties=[SAMPLE_PROPERTY, SAMPLE_PROPERTY_2],
            channel="whatsapp",
            metadata={"relaxed_filters": ["barrio eliminado"]},
        )
        intro_text = payload.messages[0].text
        # The intro might pass through WA formatting (no HTML) but content stays
        assert "5.2 millones" in intro_text
        assert "Vista Alegre" in intro_text
        assert "Querés ver esas opciones" in intro_text
        assert not intro_text.endswith("…")


# ---------------------------------------------------------------------------
# TestWizardTemplatesRemoved
# ---------------------------------------------------------------------------


class TestWizardTemplatesRemoved:
    """Verify wizard template keys are no longer returned by get_template_key."""

    def setup_method(self):
        self.fmt = WhatsAppFormatter()

    def test_no_wizard_op_template(self):
        key = self.fmt.get_template_key("busqueda_incompleta_operacion")
        assert key != "wa_tpl_wizard_op"
        assert key is None

    def test_no_wizard_zona_template(self):
        key = self.fmt.get_template_key("busqueda_incompleta_zona")
        assert key != "wa_tpl_wizard_zona"
        assert key is None

    def test_no_wizard_elegir_zona_template(self):
        key = self.fmt.get_template_key("elegir_zona")
        assert key != "wa_tpl_elegir_zona"
        assert key is None

    def test_no_wizard_tipo_template(self):
        """wa_tpl_wizard_tipo no longer returned for any intent."""
        for intent in ("busqueda_incompleta", "conversacion", "ambiguo_visita"):
            key = self.fmt.get_template_key(intent)
            assert key != "wa_tpl_wizard_tipo", f"Intent {intent} returned wizard_tipo"

    def test_result_templates_still_work(self):
        """Multi-result templates (res2, res2_con_pendientes) still carry buttons.

        v7 remodel: 1-result / detalle / 0-result / saludo all moved to plain
        text — Claude guides the user via body. Only 2+ result busqueda and
        paginacion keep a quick-reply template for clickable Ver detalle buttons.
        """
        assert self.fmt.get_template_key("busqueda", num_results=2) == "wa_tpl_res2"
        assert self.fmt.get_template_key("busqueda", num_results=2, has_pending=True) == "wa_tpl_res2_con_pendientes"
        assert self.fmt.get_template_key("busqueda", num_results=1) is None
        assert self.fmt.get_template_key("saludo") is None
        assert self.fmt.get_template_key("detalle") is None

    def test_busqueda_incompleta_returns_none(self):
        """busqueda_incompleta should return None (plain text, no template)."""
        key = self.fmt.get_template_key("busqueda_incompleta")
        assert key is None


# ---------------------------------------------------------------------------
# TestGuideBlockIntegration
# ---------------------------------------------------------------------------


class TestGuideBlockIntegration:
    """Tests that _guide_block() is wired into build_payload() output."""

    def setup_method(self):
        self.builder = ResponseBuilder()

    # --- Telegram ---

    def test_telegram_busqueda_has_guide_message(self):
        """Telegram busqueda payload: last message contains guide text."""
        payload = self.builder.build_payload(
            text="Encontré estas opciones:",
            intent="busqueda",
            properties=[SAMPLE_PROPERTY],
            channel="telegram",
        )
        last_text = payload.messages[-1].text
        assert "ajustar" in last_text.lower() or "zona" in last_text.lower() or "precio" in last_text.lower()

    def test_telegram_paginacion_has_guide_message(self):
        """Telegram paginacion payload: last message contains guide text."""
        payload = self.builder.build_payload(
            text="",
            intent="paginacion",
            properties=[SAMPLE_PROPERTY, SAMPLE_PROPERTY_2],
            channel="telegram",
        )
        last_text = payload.messages[-1].text
        assert "ajustar" in last_text.lower() or "zona" in last_text.lower()

    def test_telegram_guide_message_has_no_photo(self):
        """Guide message on Telegram is text-only (no photo)."""
        payload = self.builder.build_payload(
            text="Resultado:",
            intent="busqueda",
            properties=[SAMPLE_PROPERTY],
            channel="telegram",
        )
        last_msg = payload.messages[-1]
        assert last_msg.photo_url is None
        assert last_msg.template_id is None

    def test_telegram_guide_text_content(self):
        """Guide text for busqueda/paginacion matches _guide_block output."""
        expected = _guide_block("busqueda").strip()
        payload = self.builder.build_payload(
            text="",
            intent="busqueda",
            properties=[SAMPLE_PROPERTY],
            channel="telegram",
        )
        last_text = payload.messages[-1].text
        assert last_text == expected

    # --- WhatsApp ---

    def test_wa_busqueda_guide_embedded_in_btn_body(self):
        """WA busqueda: guide text appears in button template body, before 'Elegí una opción:'."""
        payload = self.builder.build_payload(
            text="Resultado:",
            intent="busqueda",
            properties=[SAMPLE_PROPERTY, SAMPLE_PROPERTY_2],
            channel="whatsapp",
            has_pending=False,
        )
        # Last message is the ContentSid template message
        last_msg = payload.messages[-1]
        assert last_msg.template_id is not None
        btn_body = last_msg.text
        guide = _guide_block("busqueda")
        assert guide.strip() in btn_body
        # Guide text must appear BEFORE "Elegí una opción:"
        guide_pos = btn_body.find(guide.strip())
        elegir_pos = btn_body.find("Elegí una opción:")
        assert guide_pos < elegir_pos, "Guide text must precede 'Elegí una opción:'"

    def test_wa_paginacion_guide_embedded_in_btn_body(self):
        """WA paginacion with 2+ results: guide text embedded in template body.

        v7 remodel: only 2+ result paginacion keeps a template — single-prop
        pagination now falls back to plain text (tested separately).
        """
        payload = self.builder.build_payload(
            text="",
            intent="paginacion",
            properties=[SAMPLE_PROPERTY, SAMPLE_PROPERTY_2],
            channel="whatsapp",
            has_pending=True,
        )
        last_msg = payload.messages[-1]
        assert last_msg.template_id is not None
        btn_body = last_msg.text
        assert "ajustar" in btn_body.lower() or "zona" in btn_body.lower()

    def test_wa_btn_body_still_ends_with_elegir(self):
        """WA button body must still end with 'Elegí una opción:' after guide inserted."""
        payload = self.builder.build_payload(
            text="",
            intent="busqueda",
            properties=[SAMPLE_PROPERTY, SAMPLE_PROPERTY_2],
            channel="whatsapp",
            has_pending=False,
        )
        last_msg = payload.messages[-1]
        assert last_msg.text.endswith("Elegí una opción:")

    # --- build_wa_button_summary with guide param ---

    def test_build_wa_button_summary_accepts_guide(self):
        """_build_wa_button_summary passes guide text through before 'Elegí'."""
        guide = "\n\nSi querés ajustar la búsqueda — solo decime."
        summary = self.builder._build_wa_button_summary(
            [SAMPLE_PROPERTY], has_pending=False, guide=guide,
        )
        assert "ajustar" in summary
        elegir_pos = summary.find("Elegí una opción:")
        guide_pos = summary.find("ajustar")
        assert guide_pos < elegir_pos

    def test_build_wa_button_summary_empty_guide_unchanged(self):
        """_build_wa_button_summary with guide='' produces same output as before."""
        summary_no_guide = self.builder._build_wa_button_summary(
            [SAMPLE_PROPERTY], has_pending=False,
        )
        summary_empty_guide = self.builder._build_wa_button_summary(
            [SAMPLE_PROPERTY], has_pending=False, guide="",
        )
        assert summary_no_guide == summary_empty_guide


# ---------------------------------------------------------------------------
# TestB9LeadOptOutConfirmation
# ---------------------------------------------------------------------------


class TestB9LeadOptOutConfirmation:
    """B9: lead and opt_out must produce plain text payloads (no WA template)."""

    def test_opt_out_text_constant_has_farewell(self):
        """DEFAULT_OPT_OUT_TEXT must acknowledge opt-out and leave door open (B9)."""
        assert "escribir" in DEFAULT_OPT_OUT_TEXT.lower() or "mensajes" in DEFAULT_OPT_OUT_TEXT.lower()
        assert "búsqueda" in DEFAULT_OPT_OUT_TEXT.lower() or "busqueda" in DEFAULT_OPT_OUT_TEXT.lower()

    def test_lead_payload_single_text_message(self):
        """lead intent produces a plain text payload (no template) (B9)."""
        rb = ResponseBuilder()
        lead_text = get_response_template("lead")
        payload = rb.build_payload(
            text=lead_text,
            intent="lead",
            properties=[],
            channel="whatsapp",
        )
        assert len(payload.messages) == 1
        assert payload.messages[0].template_id is None
        assert payload.messages[0].text == lead_text

    def test_opt_out_payload_single_text_message(self):
        """opt_out intent produces a plain text payload (no template) (B9)."""
        rb = ResponseBuilder()
        payload = rb.build_payload(
            text=DEFAULT_OPT_OUT_TEXT,
            intent="opt_out",
            properties=[],
            channel="whatsapp",
        )
        assert len(payload.messages) == 1
        assert payload.messages[0].template_id is None


# ---------------------------------------------------------------------------
# TestB2SaludoNoButtons
# ---------------------------------------------------------------------------


class TestB2SaludoNoButtons:
    """B2: saludo intent must not send template with buttons."""

    def test_saludo_get_template_key_returns_none(self):
        fmt = WhatsAppFormatter()
        result = fmt.get_template_key("saludo", num_results=0)
        assert result is None, "saludo should NOT use wa_tpl_saludo template (B2)"

    def test_saludo_build_buttons_returns_empty(self):
        rb = ResponseBuilder()
        buttons = rb.build_buttons("saludo")
        assert buttons == [], "saludo should return no buttons (B2)"

    def test_saludo_build_payload_single_message_no_template(self):
        rb = ResponseBuilder()
        payload = rb.build_payload(
            text="Hola! Soy el asistente de Onnix.",
            intent="saludo",
            properties=[],
            channel="whatsapp",
        )
        assert len(payload.messages) == 1, "saludo should produce a single text message (B2)"
        assert payload.messages[0].template_id is None, \
            "saludo message must not have template_id (B2)"


# ---------------------------------------------------------------------------
# M2.F3 — Caption detalle: operación + tipo de propiedad
# ---------------------------------------------------------------------------


class TestDetailCaptionOperationAndType:
    """M2.F3: el caption del detalle debe incluir tipo de operación (venta/
    alquiler) y tipo de propiedad (casa/depto/...) para dar contexto al
    cliente sin que tenga que preguntar."""

    def setup_method(self):
        self.builder = ResponseBuilder()

    def _get_caption(self, prop: dict, channel: str = "telegram") -> str:
        """Extract caption from detail payload (last photo message, N8N order)."""
        payload = self.builder.build_payload(
            text="Detalles de la propiedad:",
            intent="detalle",
            properties=[prop],
            channel=channel,
        )
        photo_msgs = [m for m in payload.messages if m.photo_url is not None]
        assert photo_msgs, "Expected at least one photo message"
        return photo_msgs[-1].text or ""

    def test_caption_includes_operation_and_type(self):
        caption = self._get_caption(SAMPLE_PROPERTY, channel="telegram")
        assert "\U0001f3f7" in caption, "Missing label emoji in caption"
        assert "Venta" in caption
        assert "Casa" in caption

    def test_caption_only_operation_when_type_missing(self):
        prop = {**SAMPLE_PROPERTY, "property_type": None}
        caption = self._get_caption(prop, channel="telegram")
        assert "\U0001f3f7" in caption
        assert "Venta" in caption

    def test_caption_only_type_when_operation_missing(self):
        prop = {**SAMPLE_PROPERTY, "operation": None}
        caption = self._get_caption(prop, channel="telegram")
        assert "\U0001f3f7" in caption
        assert "Casa" in caption

    def test_caption_omits_label_when_both_missing(self):
        prop = {**SAMPLE_PROPERTY, "operation": None, "property_type": None}
        caption = self._get_caption(prop, channel="telegram")
        assert "\U0001f3f7" not in caption, \
            "Label emoji must not appear when both operation and type are None"

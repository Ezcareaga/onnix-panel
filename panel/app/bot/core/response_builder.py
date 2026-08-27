"""ResponseBuilder — channel-polymorphic response formatting.

Formats bot output per channel: property cards, buttons, photo URLs,
and channel-specific text formatting. Produces ChannelPayload objects
ready for the channel adapter to send.

Plan 62-03: CORE-03 ResponseBuilder.
"""
from __future__ import annotations

import html
import logging
import re

from app.bot.core.types import ChannelPayload, PayloadMessage
from app.utils.money import miles

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# IC property → detail dict adapter
# ---------------------------------------------------------------------------

def ic_prop_to_detail_dict(ic_prop: "object") -> dict:
    """Convert an InfocasasProperty ORM object to a property detail dict.

    Maps IC-specific price/currency fields to the canonical price_usd/price_pyg
    keys expected by _format_price and the response formatters.

    price_sale → price_usd when currency_sale is USD, else price_pyg.
    price_rent → price_usd when currency_rent is USD, else price_pyg.
    If both sale and rent prices exist, sale takes precedence for display.

    Args:
        ic_prop: An InfocasasProperty ORM instance (or duck-typed equivalent).

    Returns:
        Dict with canonical keys: id, title, city, neighborhood, operation,
        property_type, bedrooms, bathrooms, total_area_m2, url,
        price_usd, price_pyg.
    """
    price_usd: float | None = None
    price_pyg: float | None = None

    sale = ic_prop.price_sale
    sale_cur = (ic_prop.currency_sale or "").upper()
    rent = ic_prop.price_rent
    rent_cur = (ic_prop.currency_rent or "").upper()

    if sale is not None:
        if sale_cur == "USD":
            price_usd = float(sale)
        else:
            price_pyg = float(sale)
    elif rent is not None:
        if rent_cur == "USD":
            price_usd = float(rent)
        else:
            price_pyg = float(rent)

    return {
        "id": ic_prop.id,
        "title": ic_prop.title,
        "city": ic_prop.city,
        "neighborhood": ic_prop.neighborhood,
        "operation": ic_prop.operation,
        "property_type": ic_prop.property_type,
        "bedrooms": ic_prop.bedrooms,
        "bathrooms": getattr(ic_prop, "bathrooms", None),
        "total_area_m2": getattr(ic_prop, "total_area_m2", None),
        "built_area_m2": getattr(ic_prop, "built_area_m2", None),
        "url": getattr(ic_prop, "url", None) or "",
        "price_usd": price_usd,
        "price_pyg": price_pyg,
        "price_currency": "USD" if price_usd else "PYG",
        # IC properties have no local images — no image_url
        "image_urls": [],
        "main_image_url": None,
        "local_image_count": 0,
        "source": "infocasas",
        "is_active": True,
    }

IMAGE_BASE_URL = "https://onnix.com.py/images"
TG_CAPTION_MAX = 1024
WA_BODY_MAX = 1600
WA_CAPTION_MAX = 1024
DETAIL_MAX_PHOTOS = 5
DETAIL_DESC_MAX = 800

NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣"]

# Intents that are text-only (no property cards)
TEXT_ONLY_INTENTS = frozenset({
    "saludo", "conversacion", "lead", "opt_out",
    "busqueda_incompleta", "busqueda_incompleta_operacion",
    "busqueda_incompleta_zona", "elegir_zona", "ambiguo_visita",
    "ic_welcome", "ic_reenviado_welcome", "manual_template",
})

# ---------------------------------------------------------------------------
# Helper: contextual guidance block
# ---------------------------------------------------------------------------

def _guide_block(intent: str) -> str:
    """Return a contextual guidance block appended to bot responses.

    Provides clear next-step instructions so the client always knows
    what to do after receiving a bot message.
    """
    if intent in ("busqueda", "paginacion"):
        return "\n\nSi querés ajustar la búsqueda — cambiar zona, precio o tipo — solo decime."
    elif intent == "detalle":
        return (
            "\n\nSi necesitás más info sobre financiación o coordinar una visita, "
            "un asesor puede ayudarte.\n"
            "Si no te convenció, podemos seguir buscando. Pasame:\n"
            "— Tipo de propiedad\n— Zona o barrio\n— Compra o alquiler\n"
            "— Presupuesto aproximado (opcional)"
        )
    return ""


# ---------------------------------------------------------------------------
# Helper: photo URL generation
# ---------------------------------------------------------------------------

def build_photo_urls(prop: dict, max_photos: int = 1) -> list[str]:
    """Build photo URLs for a property.

    Strategy:
    1. Use image_urls from DB (CDN URLs, jpg) — preferred for WhatsApp
       compatibility (Twilio rejects local webp via 63021).
    2. Fallback to main_image_url if image_urls is empty.
    3. Return up to max_photos URLs total, deduped.
    """
    urls: list[str] = []

    # Prefer CDN URLs from image_urls array (what N8N used)
    cdn_urls = prop.get("image_urls") or []
    if isinstance(cdn_urls, (list, tuple)):
        for url in cdn_urls:
            if isinstance(url, str) and url.strip():
                clean = url.strip()
                if clean not in urls:
                    urls.append(clean)

    # Fallback to main_image_url if image_urls was empty
    if not urls:
        main_url = (prop.get("main_image_url") or "").strip()
        if main_url:
            urls.append(main_url)

    return urls[:max_photos]


# ---------------------------------------------------------------------------
# Helper: format price
# ---------------------------------------------------------------------------

def _format_price(prop: dict) -> str:
    """Format price with thousands separator (dot for Latin America).

    Logic:
    - If price_currency is PYG and price_usd > 100_000, treat price_usd
      as actually being PYG (data quality issue in some sources).
    - If both USD and PYG are present, show both: "USD X | Gs. Y".
    - Gs. uses dot separator: 700.000.000
    - USD uses dot separator: 107.314
    """
    price_usd = prop.get("price_usd")
    price_pyg = prop.get("price_pyg")
    price_currency = prop.get("price_currency", "")

    # Guard: if currency is PYG and price_pyg is empty but price_usd > 100k,
    # that price_usd is really PYG (scraper data quality issue).
    # Only convert when price_pyg is missing — if both exist, they're genuine.
    if price_currency and price_currency.upper() == "PYG" and price_usd and price_usd > 100_000 and not price_pyg:
        price_pyg = price_usd
        price_usd = None

    if price_usd and price_pyg:
        usd_fmt = miles(price_usd)
        pyg_fmt = miles(price_pyg)
        return f"USD {usd_fmt} | Gs. {pyg_fmt}"
    if price_usd:
        formatted = miles(price_usd)
        return f"USD {formatted}"
    if price_pyg:
        formatted = miles(price_pyg)
        return f"Gs. {formatted}"
    return "Consultar precio"


def _normalize_title(title: str, max_len: int = 80) -> str:
    """Normalize property title: titlecase ALL CAPS, truncate at word boundary."""
    if not title:
        return "Propiedad"
    if title.isupper() and len(title) > 5:
        title = title.title()
    if len(title) > max_len:
        truncated = title[:max_len]
        last_space = truncated.rfind(" ")
        if last_space > max_len // 2:
            truncated = truncated[:last_space]
        title = truncated + "\u2026"
    return title


# ---------------------------------------------------------------------------
# TelegramFormatter
# ---------------------------------------------------------------------------

class TelegramFormatter:
    """Formats output for Telegram using HTML parse mode."""

    @staticmethod
    def escape_html(text: str) -> str:
        """Escape HTML entities: &, <, >."""
        return html.escape(text)

    def format_property_card(self, prop: dict, prop_index: int | None = None) -> str:
        """Build a multi-line HTML card for a property.

        Uses emoji for visual structure and <b> tags for emphasis.
        """
        title = self.escape_html(_normalize_title(prop.get("title", "Propiedad")))
        city = self.escape_html(prop.get("city", ""))
        neighborhood = self.escape_html(prop.get("neighborhood", ""))
        operation = (prop.get("operation", "venta") or "venta").capitalize()
        prop_type = (prop.get("property_type", "") or "").capitalize()
        price = _format_price(prop)

        bedrooms = prop.get("bedrooms")
        bathrooms = prop.get("bathrooms")
        total_area = prop.get("total_area_m2")

        if prop_index is not None:
            emoji = NUMBER_EMOJIS[prop_index] if prop_index < len(NUMBER_EMOJIS) else f"{prop_index + 1}."
            prop_id = prop.get("id", "")
            lines = [f"{emoji} \U0001f3e0 <b>#{prop_id} \u2014 {title}</b>"]
        else:
            lines = [f"\U0001f3e0 <b>{title}</b>"]

        # Location
        location_parts = []
        if city:
            location_parts.append(city)
        if neighborhood:
            location_parts.append(neighborhood)
        if location_parts:
            lines.append(f"\U0001f4cd {', '.join(location_parts)}")

        # Operation and type
        op_parts = []
        if operation:
            op_parts.append(operation)
        if prop_type:
            op_parts.append(prop_type)
        if op_parts:
            lines.append(f"\U0001f3f7\ufe0f {' | '.join(op_parts)}")

        # Price
        lines.append(f"\U0001f4b0 <b>{price}</b>")

        # Specs
        specs = []
        if bedrooms:
            specs.append(f"{bedrooms} dorm")
        if bathrooms:
            specs.append(f"{bathrooms} ba\u00f1o{'s' if bathrooms > 1 else ''}")
        if total_area:
            specs.append(f"{int(total_area)} m\u00b2")
        if specs:
            lines.append(f"\U0001f4d0 {' \u2022 '.join(specs)}")

        return "\n".join(lines)

    def format_caption(self, text: str) -> str:
        """Truncate text to Telegram caption limit (1024 chars)."""
        if len(text) > TG_CAPTION_MAX:
            return text[: TG_CAPTION_MAX - 3] + "..."
        return text

    def build_inline_keyboard(self, buttons: list[dict]) -> list[dict]:
        """Return buttons as-is (each has 'text' and 'callback_data')."""
        return buttons


# ---------------------------------------------------------------------------
# WhatsAppFormatter
# ---------------------------------------------------------------------------

class WhatsAppFormatter:
    """Formats output for WhatsApp using WA markdown."""

    @staticmethod
    def html_to_wa(text: str) -> str:
        """Convert HTML formatting to WhatsApp markdown.

        - <b>...</b> -> *...*
        - <i>...</i> -> _..._
        - <br> / <br/> -> newline
        - All other HTML tags stripped, content preserved.
        """
        result = text
        # Bold
        result = re.sub(r"<b>(.*?)</b>", r"*\1*", result, flags=re.DOTALL)
        # Italic
        result = re.sub(r"<i>(.*?)</i>", r"_\1_", result, flags=re.DOTALL)
        # Line breaks
        result = re.sub(r"<br\s*/?>", "\n", result)
        # Strip all remaining HTML tags
        result = re.sub(r"<[^>]+>", "", result)
        return result

    def format_property_card(self, prop: dict, prop_index: int | None = None) -> str:
        """Build a WA-formatted card using *bold* instead of HTML tags."""
        title = _normalize_title(prop.get("title", "Propiedad"))
        city = prop.get("city", "")
        neighborhood = prop.get("neighborhood", "")
        operation = (prop.get("operation", "venta") or "venta").capitalize()
        prop_type = (prop.get("property_type", "") or "").capitalize()
        price = _format_price(prop)

        bedrooms = prop.get("bedrooms")
        bathrooms = prop.get("bathrooms")
        total_area = prop.get("total_area_m2")

        if prop_index is not None:
            emoji = NUMBER_EMOJIS[prop_index] if prop_index < len(NUMBER_EMOJIS) else f"{prop_index + 1}."
            prop_id = prop.get("id", "")
            lines = [f"{emoji} \U0001f3e0 *#{prop_id} \u2014 {title}*"]
        else:
            lines = [f"\U0001f3e0 *{title}*"]

        # Location
        location_parts = []
        if city:
            location_parts.append(city)
        if neighborhood:
            location_parts.append(neighborhood)
        if location_parts:
            lines.append(f"\U0001f4cd {', '.join(location_parts)}")

        # Operation and type
        op_parts = []
        if operation:
            op_parts.append(operation)
        if prop_type:
            op_parts.append(prop_type)
        if op_parts:
            lines.append(f"\U0001f3f7\ufe0f {' | '.join(op_parts)}")

        # Price
        lines.append(f"\U0001f4b0 *{price}*")

        # Specs
        specs = []
        if bedrooms:
            specs.append(f"{bedrooms} dorm")
        if bathrooms:
            specs.append(f"{bathrooms} ba\u00f1o{'s' if bathrooms > 1 else ''}")
        if total_area:
            specs.append(f"{int(total_area)} m\u00b2")
        if specs:
            lines.append(f"\U0001f4d0 {' \u2022 '.join(specs)}")

        return "\n".join(lines)

    def format_body(self, text: str) -> str:
        """Truncate text to WA body limit (1600 chars)."""
        if len(text) > WA_BODY_MAX:
            return text[: WA_BODY_MAX - 3] + "..."
        return text

    def get_template_key(self, intent: str, num_results: int = 0, has_pending: bool = False) -> str | None:
        """Map intent to WA ContentSid template key.

        Templates are only required for intents that need **quick-reply
        buttons** rendered natively by WhatsApp. In the v7 architecture
        Claude guides the user through the body text, so most intents
        (detalle, 1-result search, 0-result search, conversacion, lead,
        busqueda_incompleta, opt_out) are plain text — Twilio sends the
        ``Body`` field without a ContentSid.

        The only intents that still need templates are multi-result
        search/pagination pages where each property gets a clickable
        ``Ver detalle`` button alongside the paginate/asesor buttons.

        Returns None for anything else (plain text).
        """
        # Multi-result search / pagination → quick-reply template with detail buttons.
        if intent in ("busqueda", "paginacion") and num_results >= 2:
            return "wa_tpl_res2_con_pendientes" if has_pending else "wa_tpl_res2"
        return None


# ---------------------------------------------------------------------------
# ResponseBuilder
# ---------------------------------------------------------------------------

class ResponseBuilder:
    """Channel-polymorphic response builder.

    Same input, different output per channel. Delegates formatting
    to TelegramFormatter or WhatsAppFormatter based on channel param.
    """

    def __init__(self) -> None:
        self._tg = TelegramFormatter()
        self._wa = WhatsAppFormatter()

    def build_property_card(self, prop: dict, channel: str = "telegram", prop_index: int | None = None) -> str:
        """Format a property dict into a channel-appropriate card."""
        if channel == "whatsapp":
            return self._wa.format_property_card(prop, prop_index=prop_index)
        return self._tg.format_property_card(prop, prop_index=prop_index)

    def build_photo_urls(self, prop: dict, max_photos: int = 1) -> list[str]:
        """Generate image URLs from property data."""
        return build_photo_urls(prop, max_photos=max_photos)

    def build_buttons(
        self,
        intent: str,
        properties: list[dict] | None = None,
        has_pending: bool = False,
        channel: str = "telegram",
    ) -> list[dict]:
        """Generate intent-appropriate button lists.

        Returns list of dicts with 'text' and 'callback_data' keys.
        """
        properties = properties or []

        if intent in ("busqueda", "paginacion"):
            buttons = []
            for idx, prop in enumerate(properties):
                emoji = NUMBER_EMOJIS[idx] if idx < len(NUMBER_EMOJIS) else f"{idx + 1}."
                prop_id = prop.get("id", "")
                neighborhood = prop.get("neighborhood") or prop.get("city") or ""
                price = _format_price(prop)
                label = f"{emoji} #{prop_id} \u2014 {neighborhood}, {price}"
                buttons.append({
                    "text": label,
                    "callback_data": f"detail_{idx + 1}",
                })
            if has_pending:
                buttons.append({
                    "text": "\u27a1\ufe0f M\u00e1s opciones",
                    "callback_data": "ver_mas",
                })
            return buttons

        # intent == "detalle": no fixed buttons — Claude guides the user in the
        # body text (hablar con asesor, más opciones, nueva búsqueda).
        # See commit removing wa_tpl_detalle/res1_con_asesor/busqueda (M4).

        if intent == "busqueda_incompleta_operacion":
            return [
                {"text": "\U0001f3e0 Comprar", "callback_data": "SEARCH_COMPRA"},
                {"text": "\U0001f511 Alquilar", "callback_data": "SEARCH_ALQUILER"},
            ]

        if intent == "busqueda_incompleta_zona":
            return []

        # conversacion, lead, opt_out, busqueda_incompleta, etc.
        return []

    def build_alternative_buttons(
        self,
        alternatives: list[dict],
        channel: str = "telegram",
    ) -> list[dict]:
        """Build one Quick Reply button per alternative (max 3).

        Label is truncated to 20 chars (Twilio Quick Reply limit).
        Payload comes from alt["callback_payload"] (already "ALT:<id>",
        <= 50 chars per Twilio constraint, set in Fase A).

        Args:
            alternatives: List of alternative dicts from metadata["alternatives"].
                Each must have "label" and "callback_payload".
            channel: "whatsapp" or "telegram".

        Returns:
            List of button dicts with "text" and "callback_data" keys.
        """
        buttons = []
        for alt in alternatives[:3]:  # max 3, already capped by AlternativesBuilder
            raw_label = alt.get("label", "")
            if len(raw_label) > 20:
                label = raw_label[:19] + "\u2026"  # ellipsis within 20 chars
            else:
                label = raw_label
            payload = alt.get("callback_payload", "")
            buttons.append({
                "text": label,
                "callback_data": payload,
            })
        return buttons

    def _build_wa_button_summary(
        self,
        properties: list[dict],
        has_pending: bool,
        guide: str = "",
    ) -> str:
        """Build button summary text for WA ContentSid template.

        Includes price per property for quick scanning.
        The 'hablar con asesor' text hint is omitted — it's redundant
        when the template already has an asesor button.
        Optional guide text (e.g. from _guide_block) is inserted before
        the final "Elegí una opción:" prompt.
        """
        lines = []
        for idx, prop in enumerate(properties):
            emoji = NUMBER_EMOJIS[idx] if idx < len(NUMBER_EMOJIS) else f"{idx + 1}."
            prop_id = prop.get("id", "")
            zone = prop.get("neighborhood") or prop.get("city") or ""
            price = _format_price(prop)
            lines.append(f"{emoji} *#{prop_id}* \u2014 {zone} ({price})")

        text = "\n".join(lines)
        if has_pending:
            text += "\n\nToc\u00e1 *\u27a1\ufe0f M\u00e1s opciones* para seguir viendo"
        if guide:
            text += guide  # guide already starts with \n\n
        text += "\n\nEleg\u00ed una opci\u00f3n:"
        return text

    def build_payload(
        self,
        text: str,
        intent: str,
        properties: list[dict],
        channel: str,
        has_pending: bool = False,
        metadata: dict | None = None,
    ) -> ChannelPayload:
        """Assemble a full ChannelPayload ready for the channel adapter.

        Handles text-only intents, busqueda/paginacion with property cards,
        detalle with photo galleries, and zero-results with alternative buttons
        when ``metadata["alternatives"]`` is present.
        """
        metadata = metadata or {}
        messages: list[PayloadMessage] = []

        # Determine template_id for WhatsApp
        template_id = None
        if channel == "whatsapp":
            template_id = self._wa.get_template_key(intent, num_results=len(properties), has_pending=has_pending)

        # --- Text-only intents or no properties ---
        if intent in TEXT_ONLY_INTENTS or not properties:
            formatted_text = text
            if channel == "whatsapp":
                formatted_text = self._wa.format_body(text)

            # Intents with separate button templates on WA: send text first, then template.
            # All wizard intents removed — plain text now. No split-template intents remain.
            _SPLIT_TEMPLATE_INTENTS = ()
            if intent in _SPLIT_TEMPLATE_INTENTS and channel == "whatsapp" and template_id:
                messages.append(PayloadMessage(text=formatted_text))
                messages.append(PayloadMessage(
                    text="Elegí una opción:",
                    template_id=template_id,
                ))
                return ChannelPayload(messages=messages, channel=channel)

            # Alternative buttons: inject after Claude's natural text when
            # metadata["alternatives"] is present (Fase F — zero-results path).
            alt_buttons: list[dict] = []
            if metadata.get("alternatives"):
                alt_buttons = self.build_alternative_buttons(
                    metadata["alternatives"], channel=channel
                )

            messages.append(PayloadMessage(
                text=formatted_text,
                template_id=template_id,
                buttons=alt_buttons,
            ))
            return ChannelPayload(messages=messages, channel=channel)

        # --- Busqueda / Paginacion: one message per property ---
        if intent in ("busqueda", "paginacion"):
            guide = _guide_block(intent)

            # Add Claude's introductory text before property cards.
            # When the search relaxed filters (metadata['relaxed_filters'] non-empty),
            # the intro carries the explanation of WHAT was relaxed and WHY \u2014 it
            # must reach the client whole, even if it exceeds the standard 150-char
            # cap used for normal "Encontr\u00e9 X resultados" intros.
            if text and text.strip():
                intro = text.strip()
                relaxed = metadata.get("relaxed_filters") or []
                if not relaxed and len(intro) > 150:
                    intro = intro[:150] + "\u2026"
                messages.append(PayloadMessage(text=intro))

            buttons = self.build_buttons(
                intent=intent,
                properties=properties,
                has_pending=has_pending,
                channel=channel,
            )

            for idx, prop in enumerate(properties):
                card = self.build_property_card(prop, channel=channel, prop_index=idx)
                photo_urls = self.build_photo_urls(prop)
                first_photo = photo_urls[0] if photo_urls else None
                is_last = idx == len(properties) - 1

                if channel == "telegram":
                    card = self._tg.format_caption(card)
                    msg_buttons = buttons if is_last else []
                    msg_template = template_id if idx == 0 else None
                else:
                    # WhatsApp: photo + caption only; buttons go
                    # in a separate ContentSid message after all photos
                    if len(card) > WA_CAPTION_MAX:
                        card = card[:WA_CAPTION_MAX - 3] + "..."
                    msg_buttons = []
                    msg_template = None

                messages.append(PayloadMessage(
                    text=card,
                    photo_url=first_photo,
                    buttons=msg_buttons,
                    template_id=msg_template,
                ))

            # WhatsApp: separate button template message after all photos,
            # with guide text embedded before "Elegí una opción:".
            if channel == "whatsapp" and template_id:
                btn_body = self._build_wa_button_summary(properties, has_pending, guide=guide)
                messages.append(PayloadMessage(
                    text=btn_body,
                    template_id=template_id,
                ))
            elif guide:
                # Telegram (or WA without template): guide as final text message
                messages.append(PayloadMessage(text=guide.strip()))

            return ChannelPayload(messages=messages, channel=channel)

        # --- Detalle: photo gallery + rich caption + action buttons ---
        if intent == "detalle" and properties:
            prop = properties[0]
            photo_urls = self.build_photo_urls(prop, max_photos=DETAIL_MAX_PHOTOS)
            logger.info(
                "Detail photo URLs for property %s: %d URLs (max=%d, channel=%s) — %s",
                prop.get("id"), len(photo_urls), DETAIL_MAX_PHOTOS, channel,
                [u.split("/")[-1] for u in photo_urls],
            )
            buttons = self.build_buttons(intent="detalle", properties=properties, has_pending=has_pending, channel=channel)

            # Claude's text goes as a short intro message before the photo card
            if text and text.strip() and len(text.strip()) > 10:
                intro = text.strip()[:200]
                if channel == "whatsapp":
                    intro = self._wa.format_body(intro)
                messages.append(PayloadMessage(text=intro))

            # Build rich caption for the first photo
            title = _normalize_title(prop.get("title", "Propiedad"), max_len=80)
            price = _format_price(prop)
            address = prop.get("address", "")

            caption_lines = []
            if channel == "whatsapp":
                caption_lines.append(f"\U0001f3e0 *{title}*")
                caption_lines.append(f"\U0001f4b0 *{price}*")
            else:
                caption_lines.append(f"\U0001f3e0 <b>{title}</b>")
                caption_lines.append(f"\U0001f4b0 <b>{price}</b>")

            # Location
            city = prop.get("city", "")
            neighborhood = prop.get("neighborhood", "")
            loc_parts = [p for p in [city, neighborhood] if p]
            if loc_parts:
                caption_lines.append(f"\U0001f4cd {', '.join(loc_parts)}")
            if address:
                caption_lines.append(f"\U0001f4cc {address}")

            # Operation + property type — gives customer full context on
            # the listing without having to ask.
            op_type_parts: list[str] = []
            operation = prop.get("operation")
            property_type = prop.get("property_type")
            if operation:
                op_type_parts.append(str(operation).capitalize())
            if property_type:
                op_type_parts.append(str(property_type).capitalize())
            if op_type_parts:
                caption_lines.append(
                    f"\U0001f3f7\ufe0f {' | '.join(op_type_parts)}"
                )

            # Specs
            specs = []
            bedrooms = prop.get("bedrooms")
            bathrooms = prop.get("bathrooms")
            total_area = prop.get("total_area_m2")
            if bedrooms:
                specs.append(f"{bedrooms} dorm")
            if bathrooms:
                specs.append(f"{bathrooms} ba\u00f1o{'s' if bathrooms and bathrooms > 1 else ''}")
            if total_area:
                specs.append(f"{int(total_area)} m\u00b2")
            if specs:
                caption_lines.append(f"\U0001f4d0 {' \u2022 '.join(specs)}")

            # Description: use DB description, sanitize HTML, fit within caption limit
            desc_raw = (prop.get("description") or "")[:DETAIL_DESC_MAX]
            if desc_raw:
                # Sanitize HTML: <br> → newline, strip all other tags
                desc_raw = re.sub(r"<br\s*/?>", "\n", desc_raw)
                desc_raw = re.sub(r"<[^>]+>", "", desc_raw)
                desc_raw = re.sub(r"\n{3,}", "\n\n", desc_raw)
                # Calculate available space for description on WhatsApp
                header_caption = "\n".join(caption_lines)
                desc_prefix = "\n\U0001f4dd "
                if channel == "whatsapp":
                    available = WA_CAPTION_MAX - len(header_caption) - len(desc_prefix)
                    if available < len(desc_raw) and available > 0:
                        # Truncate at last period before limit
                        truncated = desc_raw[:available]
                        last_dot = truncated.rfind(".")
                        if last_dot > available // 2:
                            desc_raw = truncated[: last_dot + 1] + ".."
                        else:
                            desc_raw = truncated.rstrip() + "..."
                caption_lines.append(f"{desc_prefix}{desc_raw}")

            # Google Maps link when coordinates are available
            # Skip if the description already contains a maps link
            lat = prop.get("latitude")
            lng = prop.get("longitude")
            if lat is not None and lng is not None:
                caption_so_far = "\n".join(caption_lines).lower()
                has_maps_link = any(
                    x in caption_so_far
                    for x in ("maps.google", "maps.app.goo.gl", "goo.gl/maps")
                )
                if not has_maps_link:
                    caption_lines.append(
                        f"\U0001f4cd Ver en mapa: https://maps.google.com/?q={lat},{lng}"
                    )

            caption = "\n".join(caption_lines)

            if channel == "whatsapp":
                caption = self._wa.format_body(caption)
            elif channel == "telegram":
                caption = self._tg.format_caption(caption)

            # Send extra photos first (no caption), then last photo with
            # the full caption.  User scrolls through all images and reads
            # the complete detail at the end — same order N8N used.
            if len(photo_urls) > 1:
                for url in photo_urls[:-1]:
                    messages.append(PayloadMessage(text="", photo_url=url))

            last_photo = photo_urls[-1] if photo_urls else None
            messages.append(PayloadMessage(
                text=caption,
                photo_url=last_photo,
            ))

            # Telegram: inline keyboard on last message
            if channel == "telegram" and buttons:
                messages[-1].buttons = buttons

            # WhatsApp: separate button template message after photos
            if channel == "whatsapp" and template_id:
                messages.append(PayloadMessage(
                    text="Qué te gustaría hacer?",
                    template_id=template_id,
                ))

            n_photo = sum(1 for m in messages if m.photo_url)
            n_text = sum(1 for m in messages if not m.photo_url and not m.template_id)
            n_tmpl = sum(1 for m in messages if m.template_id)
            logger.info(
                "Detail payload: %d messages (%d photo, %d text, %d template) — channel=%s",
                len(messages), n_photo, n_text, n_tmpl, channel,
            )

            return ChannelPayload(messages=messages, channel=channel)

        # --- Fallback: just text ---
        messages.append(PayloadMessage(
            text=text,
            template_id=template_id,
        ))
        return ChannelPayload(messages=messages, channel=channel)

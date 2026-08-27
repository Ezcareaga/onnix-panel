"""InfoCasas lead parser — pure logic, no DB access.

Parses raw GraphQL leadById payloads from the InfoCasas API into
database-ready :class:`ParsedLead` dataclasses.

Responsibilities
----------------
- Phone normalization to E.164 (Paraguay-aware).
- Display-name derivation with email-prefix fallback.
- Relative and absolute date parsing (InfoCasas uses both).
- Consulta-ID extraction from notification URLs.
- Notification filtering (skip old seen notifications).
- Zone selection for WhatsApp welcome templates.
- Lead data builder that combines all of the above.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.tz import PYT

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phone normalization
# ---------------------------------------------------------------------------

_DIGITS_RE = re.compile(r"[^\d+]")
_REASSIGNED_RE = re.compile(r"consulta\s+reenviada", re.IGNORECASE)
_REASSIGNED_CHARS_RE = re.compile(
    r"La propiedad consultada ten[ií]a las siguientes caracter[ií]sticas[:\s]*"
    r"(?P<tipo>[A-Za-záéíóúÁÉÍÓÚñÑ][A-Za-záéíóúÁÉÍÓÚñÑ\s]+?)\s+en\s+"
    r"(?P<operacion>\w+)\s+de\s*"
    r"(?:(?P<dorms>\d+)\s+dorms?\.?)?\s*en\s+(?P<zona>[^,]+(?:,\s*[A-Za-záéíóúÁÉÍÓÚñÑ][A-Za-záéíóúÁÉÍÓÚñÑ\s]*[A-Za-záéíóúÁÉÍÓÚñÑ])?),\s*"
    r"(?:(?P<area>[\d.,]+)\s*m[²2])?\s*por\s+"
    r"(?:USD\s*(?P<price_usd>[\d.,]+)|Gs\.?\s*(?P<price_gs>[\d.,]+))",
    re.IGNORECASE | re.DOTALL,
)


def normalize_phone(raw_phone: str | None) -> str | None:
    """Normalize a raw phone string to E.164 format.

    Paraguay-aware: local 09xxxxxxxx numbers get +595 country code.

    Args:
        raw_phone: Raw phone string from InfoCasas API, may be None, empty,
            or in any format (spaces, dashes, parentheses, etc.).

    Returns:
        E.164-formatted phone string (e.g. ``"+595981234567"``) or ``None``
        if the input cannot be resolved to a plausible phone number.
    """
    if not raw_phone:
        return None

    # Strip everything except digits and leading '+'
    cleaned = _DIGITS_RE.sub("", raw_phone)

    if not cleaned:
        return None

    candidate: str

    if cleaned.startswith("+"):
        # Already in E.164 — keep as-is
        candidate = cleaned
    elif re.match(r"^09\d{8}$", cleaned):
        # Paraguayan mobile without country code: 09XXXXXXXX → +595 9XXXXXXXX
        candidate = "+595" + cleaned[1:]
    elif cleaned.startswith("0") and len(cleaned) <= 11:
        # Local number with leading 0: strip 0, add +595
        candidate = "+595" + cleaned[1:]
        if len(candidate) != 13:
            return None
    elif re.match(r"^5950\d{9}$", cleaned):
        # InfoCasas API intercalates the local leading '0' into the number:
        # 5950XXXXXXXXX (595 + local-0 + 9 digits) → strip the 0 → +595XXXXXXXXX
        # Example: 5950975639930 → +595975639930 (Cresencia Caballero real case)
        candidate = "+595" + cleaned[4:]
    elif re.match(r"^595\d{9,10}$", cleaned):
        # Country code without '+': 595XXXXXXXXX → +595XXXXXXXXX
        candidate = "+" + cleaned
    elif 10 <= len(cleaned) <= 15:
        # Best-effort: prepend '+' for international-length numbers
        candidate = "+" + cleaned
    else:
        return None

    # Final validation: must start with '+' and be 10-15 chars (E.164 range +1..+14 digits)
    if not candidate.startswith("+") or not (10 <= len(candidate) <= 16):
        return None

    return candidate


# ---------------------------------------------------------------------------
# Name derivation
# ---------------------------------------------------------------------------

_EMAIL_PREFIX_CLEAN_RE = re.compile(r"[._\-]")
_TRAILING_DIGITS_RE = re.compile(r"\d+$")
_SENTINEL_NAMES = {"sin nombre", ""}


def derive_name(name: str | None, email: str | None) -> str:
    """Derive a display name from a raw name or email address.

    Falls back to the email prefix when the name is empty or is the
    sentinel value ``"Sin nombre"``.  If neither is available, returns
    ``"Sin nombre (InfoCasas)"``.

    Args:
        name: Raw name from InfoCasas ``from.name`` field.
        email: Email address from InfoCasas ``from.email`` field.

    Returns:
        Human-readable display name, always non-empty.

    Examples:
        >>> derive_name("Nicole Cáceres", None)
        'Nicole Cáceres'
        >>> derive_name("", "nicole.caceres123@gmail.com")
        'Nicole Caceres (InfoCasas)'
        >>> derive_name(None, None)
        'Sin nombre (InfoCasas)'
    """
    normalized_name = (name or "").strip()

    if normalized_name.lower() not in _SENTINEL_NAMES:
        return normalized_name

    # Name is empty or sentinel — try to derive from email
    if not email or "@" not in email:
        return "Sin nombre (InfoCasas)"

    prefix = email.split("@")[0]
    # Remove trailing digits
    prefix = _TRAILING_DIGITS_RE.sub("", prefix)
    # Replace separators with spaces
    prefix = _EMAIL_PREFIX_CLEAN_RE.sub(" ", prefix)
    # Title-case
    derived = prefix.strip().title()

    if not derived:
        return "Sin nombre (InfoCasas)"

    return f"{derived} (InfoCasas)"


# ---------------------------------------------------------------------------
# Relative date parsing
# ---------------------------------------------------------------------------

_RELATIVE_DATE_RE = re.compile(
    r"hace\s+(\d+)\s+(minuto|hora|d[ií]a|semana|mes)s?",
    re.IGNORECASE,
)
_ABSOLUTE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_DMY_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")

_UNIT_MAP: dict[str, str] = {
    "minuto": "minutes",
    "hora": "hours",
    "dia": "days",
    "día": "days",
    "semana": "weeks",
    "mes": "months",
}


def parse_relative_date(date_str: str | None) -> datetime:
    """Parse an InfoCasas date string into a UTC :class:`datetime`.

    InfoCasas sends dates in three formats:

    - Absolute ISO-like: ``"2026-03-28 14:30:00"``
    - Relative Spanish: ``"hace 5 horas"``, ``"hace 1 día"``
    - DD/MM/YYYY: ``"28/03/2026"``
    - Empty / None → current UTC time

    **Las fechas sin huso vienen en hora de Paraguay, no en UTC.** Hasta el
    2026-08-24 este parser las etiquetaba UTC y `consulta_date` quedaba tres
    horas en el pasado; como `lead_service._enrich_lead_row` usa ese campo como
    «último contacto» de los leads de InfoCasas, el panel le decía «hace 3 h» a
    un lead recién entrado. Medido sobre los 714 leads que pasaron por esta
    rama: el desfase contra `created_at` **nunca baja de 180,05 minutos** y 313
    caen entre 180 y 185 — un piso de exactamente 3 h no lo produce una demora
    física, lo produce una etiqueta de huso equivocada. La rama relativa
    ("hace N minutos") siempre estuvo bien: sus desfases van de 1 a 14 minutos.

    Args:
        date_str: Raw date string from the InfoCasas API.

    Returns:
        UTC-aware :class:`datetime`.  Falls back to ``datetime.now(timezone.utc)``
        when the input cannot be parsed.
    """
    now = datetime.now(timezone.utc)

    if not date_str or not date_str.strip():
        return now

    stripped = date_str.strip()

    # --- Relative date: "hace N unit(s)" ---
    match = _RELATIVE_DATE_RE.search(stripped)
    if match:
        amount = int(match.group(1))
        raw_unit = match.group(2).lower()
        unit = _UNIT_MAP.get(raw_unit)
        if unit is None:
            logger.warning("parse_relative_date: unknown unit %r in %r", raw_unit, date_str)
            return now
        if unit == "months":
            # Approximate: 30 days per month
            return now - timedelta(days=30 * amount)
        return now - timedelta(**{unit: amount})

    # --- Absolute ISO: "2026-03-28 14:30:00" ---
    if _ABSOLUTE_DATE_RE.match(stripped):
        try:
            # Normalize space separator to 'T' for fromisoformat
            iso_str = stripped.replace(" ", "T")
            # Sin huso = hora de Paraguay (ver docstring). Si algún día
            # InfoCasas empieza a mandar el offset, se respeta el que venga.
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=PYT)
            return dt.astimezone(timezone.utc)
        except ValueError:
            logger.warning("parse_relative_date: failed to parse absolute date %r", date_str)
            return now

    # --- DD/MM/YYYY ---
    dmy_match = _DMY_DATE_RE.match(stripped)
    if dmy_match:
        day, month, year = int(dmy_match.group(1)), int(dmy_match.group(2)), int(dmy_match.group(3))
        try:
            # Misma fuente, mismo huso: medianoche en Paraguay, no en UTC.
            return datetime(year, month, day, tzinfo=PYT).astimezone(timezone.utc)
        except ValueError:
            logger.warning("parse_relative_date: invalid DD/MM/YYYY value %r", date_str)
            return now

    logger.debug("parse_relative_date: unrecognized format %r, returning now", date_str)
    return now


# ---------------------------------------------------------------------------
# Consulta ID extraction
# ---------------------------------------------------------------------------

_CONSULTA_ID_RE = re.compile(r"[?&]id=(\d+)")


def extract_consulta_id(url: str | None) -> str | None:
    """Extract the numeric consulta ID from an InfoCasas notification URL.

    Args:
        url: Notification URL, e.g.
            ``"/sitio/index.php?mid=consultas&id=66065340"``.

    Returns:
        The ID as a string (e.g. ``"66065340"``) or ``None`` if not found.
    """
    if not url:
        return None
    match = _CONSULTA_ID_RE.search(url)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Notification filtering
# ---------------------------------------------------------------------------

_STARTS_WITH_HACE_RE = re.compile(r"^\s*hace\b", re.IGNORECASE)


def should_process_notification(notification: dict) -> bool:
    """Decide whether a notification should be processed.

    Filtering rules:

    - Unseen (``seen=False``) → always process.
    - Seen + relative date (starts with "hace") → process (recently seen).
    - Seen + absolute date → skip (already processed previously).

    Args:
        notification: Raw notification dict from the InfoCasas polling
            response.  Expected keys: ``seen`` (bool), ``created_at`` (str).

    Returns:
        ``True`` if the notification should be fetched and processed.
    """
    seen: bool = bool(notification.get("seen", False))

    if not seen:
        return True

    created_at: str = notification.get("created_at") or ""
    # If the date is relative ("hace ..."), it was recently created → process
    if _STARTS_WITH_HACE_RE.match(created_at):
        return True

    # Seen + absolute date → skip
    return False


# ---------------------------------------------------------------------------
# Zone selection
# ---------------------------------------------------------------------------

def select_zone(
    matched_property: dict | None,
    listing_city: str | None,
    listing_title: str | None,
) -> str:
    """Select a zone string for the WhatsApp welcome template.

    Priority order:

    1. ``matched_property["city"]`` — city from the matched property record.
    2. ``listing_city`` — neighborhood/city from the listing.
    3. ``listing_title[:30]`` — truncated listing title as last resort.
    4. ``"tu zona"`` — generic fallback.

    Args:
        matched_property: Property dict that matched the lead's listing, or
            ``None`` if no match was found.
        listing_city: City/neighborhood name from ``listing.neighborhood.name``.
        listing_title: Full listing title string.

    Returns:
        A short zone string suitable for use in a WhatsApp template message.
    """
    if matched_property:
        city = matched_property.get("city")
        if city:
            return str(city)

    if listing_city:
        return listing_city

    if listing_title:
        return listing_title[:30]

    return "tu zona"


# ---------------------------------------------------------------------------
# Lead dataclass + builder
# ---------------------------------------------------------------------------


@dataclass
class ParsedLead:
    """Parsed lead data ready for database operations.

    All fields are populated from a single GraphQL ``leadById`` response.
    Phone is normalized to E.164; dates are UTC-aware datetimes.

    Attributes:
        consulta_id: InfoCasas numeric ID for idempotency checks.
        name: Display name (derived if raw name was empty).
        phone: E.164 phone number or ``None`` if unavailable/invalid.
        email: Email address or ``None``.
        message: Lead inquiry message or ``None``.
        consulta_date: UTC datetime the inquiry was created.
        property_code: ``listing.code`` (e.g. ``"OF23CE"``) or ``None``.
        property_title: Human-readable listing title or ``None``.
        listing_city: City/neighbourhood from the listing or ``None``.
        has_whatsapp: Whether the lead has a WhatsApp-verified number.
        is_reassigned: Whether this lead was forwarded by InfoCasas from a
            competitor listing (detected via "consulta reenviada" in message).
    """

    consulta_id: str
    name: str
    phone: str | None  # E.164 or None
    email: str | None
    message: str | None
    consulta_date: datetime
    property_code: str | None  # listing.code
    property_title: str | None
    listing_city: str | None
    has_whatsapp: bool
    is_reassigned: bool  # True when message contains "consulta reenviada"
    # Characteristics extracted from reenviado message block
    listing_type: str | None = None       # "casa", "departamento", etc.
    listing_operation: str | None = None  # "venta", "alquiler"
    listing_bedrooms: int | None = None
    listing_area_m2: float | None = None
    listing_price: float | None = None
    listing_currency: str | None = None   # "usd" or "gs"
    listing_zone_from_message: str | None = None  # zona extraída del bloque de características


def parse_lead(lead_data: dict) -> ParsedLead | None:
    """Parse a raw GraphQL ``leadById`` response into a :class:`ParsedLead`.

    Returns ``None`` when there is insufficient contact information (no phone
    AND no email), since we cannot route the lead anywhere.

    Phone resolution follows InfoCasas priority:

    1. ``from.whatsapp_phone`` when ``from.has_whatsapp`` is ``True``.
    2. ``from.phone`` otherwise.

    Args:
        lead_data: Raw dict from the InfoCasas GraphQL ``leadById`` query.
            Expected shape::

                {
                    "id": "66065340",
                    "message": "Hola...",
                    "created_at": "2026-03-28 14:30:00",
                    "from": {
                        "name": "Nicole Cáceres",
                        "email": "",
                        "phone": "+595900000001",
                        "whatsapp_phone": null,
                        "has_whatsapp": false
                    },
                    "listing": {
                        "id": "193572330",
                        "title": "Casa en Alquiler...",
                        "code": "OF23CE",
                        "neighborhood": {"name": "Fernando de la Mora"}
                    }
                }

    Returns:
        :class:`ParsedLead` on success, ``None`` if contact info is missing.
    """
    consulta_id: str = str(lead_data.get("id") or "")
    if not consulta_id:
        logger.warning("parse_lead: lead_data missing 'id' field — skipping")
        return None

    from_data: dict = lead_data.get("from") or {}
    listing_data: dict = lead_data.get("listing") or {}
    neighborhood_data: dict = listing_data.get("neighborhood") or {}

    # --- Contact info ---
    raw_name: str | None = from_data.get("name") or None
    raw_email: str | None = from_data.get("email") or None
    has_whatsapp: bool = bool(from_data.get("has_whatsapp", False))

    # Phone: prefer whatsapp_phone when has_whatsapp is set
    raw_wa_phone: str | None = from_data.get("whatsapp_phone") or None
    raw_phone: str | None = from_data.get("phone") or None

    if has_whatsapp and raw_wa_phone:
        resolved_raw_phone = raw_wa_phone
    else:
        resolved_raw_phone = raw_phone

    phone = normalize_phone(resolved_raw_phone)
    email = raw_email if raw_email else None

    # Must have at least one contact channel
    if phone is None and email is None:
        logger.info(
            "parse_lead: consulta_id=%s has no phone or email — discarding",
            consulta_id,
        )
        return None

    name = derive_name(raw_name, raw_email)
    message: str | None = lead_data.get("message") or None
    is_reassigned: bool = bool(_REASSIGNED_RE.search(message or ""))
    consulta_date = parse_relative_date(lead_data.get("created_at"))

    # Extract characteristics from reenviado message block
    listing_type: str | None = None
    listing_operation: str | None = None
    listing_bedrooms: int | None = None
    listing_area_m2: float | None = None
    listing_price: float | None = None
    listing_currency: str | None = None
    listing_zone_from_message: str | None = None
    if is_reassigned and message:
        m = _REASSIGNED_CHARS_RE.search(message)
        if m:
            listing_type = m.group("tipo").strip().lower() if m.group("tipo") else None
            listing_operation = m.group("operacion").strip().lower() if m.group("operacion") else None
            listing_bedrooms = int(m.group("dorms")) if m.group("dorms") else None
            listing_area_m2 = float(m.group("area").replace(",", ".")) if m.group("area") else None
            listing_zone_from_message = m.group("zona").strip() if m.group("zona") else None
            if m.group("price_usd"):
                listing_price = float(m.group("price_usd").replace(".", "").replace(",", "."))
                listing_currency = "usd"
            elif m.group("price_gs"):
                listing_price = float(m.group("price_gs").replace(".", "").replace(",", "."))
                listing_currency = "gs"

    # --- Listing info ---
    property_code: str | None = listing_data.get("code") or None
    property_title: str | None = listing_data.get("title") or None
    listing_city: str | None = neighborhood_data.get("name") or None

    return ParsedLead(
        consulta_id=consulta_id,
        name=name,
        phone=phone,
        email=email,
        message=message,
        consulta_date=consulta_date,
        property_code=property_code,
        property_title=property_title,
        listing_city=listing_city,
        has_whatsapp=has_whatsapp,
        is_reassigned=is_reassigned,
        listing_type=listing_type,
        listing_operation=listing_operation,
        listing_bedrooms=listing_bedrooms,
        listing_area_m2=listing_area_m2,
        listing_price=listing_price,
        listing_currency=listing_currency,
        listing_zone_from_message=listing_zone_from_message,
    )

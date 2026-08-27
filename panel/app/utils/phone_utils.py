import phonenumbers

# Curated list: PY first, all LATAM, Norteamérica, all Europe, key global
PREFIXES = [
    # --- Paraguay (default) ---
    ("+595", "PY", "Paraguay"),
    # --- LATAM ---
    ("+54", "AR", "Argentina"),
    ("+55", "BR", "Brasil"),
    ("+598", "UY", "Uruguay"),
    ("+56", "CL", "Chile"),
    ("+57", "CO", "Colombia"),
    ("+51", "PE", "Perú"),
    ("+591", "BO", "Bolivia"),
    ("+593", "EC", "Ecuador"),
    ("+58", "VE", "Venezuela"),
    ("+52", "MX", "México"),
    ("+506", "CR", "Costa Rica"),
    ("+507", "PA", "Panamá"),
    ("+502", "GT", "Guatemala"),
    ("+504", "HN", "Honduras"),
    ("+503", "SV", "El Salvador"),
    ("+505", "NI", "Nicaragua"),
    ("+53", "CU", "Cuba"),
    ("+1", "DO", "Rep. Dominicana"),
    ("+1", "PR", "Puerto Rico"),
    ("+509", "HT", "Haití"),
    ("+1", "JM", "Jamaica"),
    ("+1", "TT", "Trinidad y Tobago"),
    ("+592", "GY", "Guyana"),
    ("+597", "SR", "Surinam"),
    ("+501", "BZ", "Belice"),
    # --- Norteamérica ---
    ("+1", "US", "Estados Unidos"),
    ("+1", "CA", "Canadá"),
    # --- Europa ---
    ("+34", "ES", "España"),
    ("+351", "PT", "Portugal"),
    ("+33", "FR", "Francia"),
    ("+39", "IT", "Italia"),
    ("+49", "DE", "Alemania"),
    ("+44", "GB", "Reino Unido"),
    ("+31", "NL", "Países Bajos"),
    ("+32", "BE", "Bélgica"),
    ("+41", "CH", "Suiza"),
    ("+43", "AT", "Austria"),
    ("+46", "SE", "Suecia"),
    ("+47", "NO", "Noruega"),
    ("+45", "DK", "Dinamarca"),
    ("+358", "FI", "Finlandia"),
    ("+353", "IE", "Irlanda"),
    ("+48", "PL", "Polonia"),
    ("+420", "CZ", "Chequia"),
    ("+421", "SK", "Eslovaquia"),
    ("+40", "RO", "Rumanía"),
    ("+36", "HU", "Hungría"),
    ("+30", "GR", "Grecia"),
    ("+385", "HR", "Croacia"),
    ("+381", "RS", "Serbia"),
    ("+359", "BG", "Bulgaria"),
    ("+386", "SI", "Eslovenia"),
    ("+372", "EE", "Estonia"),
    ("+371", "LV", "Letonia"),
    ("+370", "LT", "Lituania"),
    ("+380", "UA", "Ucrania"),
    ("+375", "BY", "Bielorrusia"),
    ("+7", "RU", "Rusia"),
    ("+90", "TR", "Turquía"),
    ("+354", "IS", "Islandia"),
    ("+352", "LU", "Luxemburgo"),
    ("+356", "MT", "Malta"),
    ("+357", "CY", "Chipre"),
    ("+355", "AL", "Albania"),
    ("+387", "BA", "Bosnia y Herzegovina"),
    ("+382", "ME", "Montenegro"),
    ("+389", "MK", "Macedonia del Norte"),
    ("+373", "MD", "Moldavia"),
    ("+383", "XK", "Kosovo"),
    # --- Medio Oriente ---
    ("+972", "IL", "Israel"),
    ("+971", "AE", "Emiratos Árabes"),
    ("+966", "SA", "Arabia Saudita"),
    ("+974", "QA", "Catar"),
    ("+965", "KW", "Kuwait"),
    ("+962", "JO", "Jordania"),
    ("+961", "LB", "Líbano"),
    # --- Asia ---
    ("+86", "CN", "China"),
    ("+81", "JP", "Japón"),
    ("+82", "KR", "Corea del Sur"),
    ("+91", "IN", "India"),
    ("+852", "HK", "Hong Kong"),
    ("+886", "TW", "Taiwán"),
    ("+65", "SG", "Singapur"),
    ("+60", "MY", "Malasia"),
    ("+66", "TH", "Tailandia"),
    ("+63", "PH", "Filipinas"),
    ("+62", "ID", "Indonesia"),
    ("+84", "VN", "Vietnam"),
    ("+92", "PK", "Pakistán"),
    # --- África ---
    ("+27", "ZA", "Sudáfrica"),
    ("+20", "EG", "Egipto"),
    ("+212", "MA", "Marruecos"),
    ("+234", "NG", "Nigeria"),
    # --- Oceanía ---
    ("+61", "AU", "Australia"),
    ("+64", "NZ", "Nueva Zelanda"),
]

# Quick lookup: region code -> prefix tuple
_REGION_MAP = {p[1]: p for p in PREFIXES}
# Quick lookup: dial code -> prefix tuple
_CODE_MAP = {p[0]: p for p in PREFIXES}

# Map small territories to their parent country for display purposes.
# phonenumbers sometimes returns GG/JE/IM for UK mobile numbers, etc.
_REGION_ALIASES = {
    "GG": "GB", "JE": "GB", "IM": "GB",       # Crown Dependencies → UK
    "CX": "AU", "CC": "AU",                     # Australian territories
    "AX": "FI",                                  # Åland → Finland
    "BL": "FR", "MF": "FR", "GP": "FR",         # French overseas
    "MQ": "FR", "GF": "FR", "RE": "FR",
    "YT": "FR", "PM": "FR", "WF": "FR", "NC": "FR",
    "SJ": "NO",                                  # Svalbard → Norway
    "EH": "MA",                                  # Western Sahara → Morocco
    "BQ": "NL", "CW": "NL", "SX": "NL",         # Dutch Caribbean
}


def normalize_phone(phone: str) -> tuple[str | None, str | None]:
    """Validate and normalize a phone number input to E.164.

    Returns (normalized_e164_or_None, error_or_None).
    - phone == ''  → (None, None) — optional field, OK to clear.
    - phone valid  → (E.164 string, None).
    - phone invalid → (None, 'Teléfono inválido').

    Accepts PY national format (e.g. '0981123456') or E.164 ('+595981123456').
    Uses 'PY' as the default region for numbers without an explicit country code.
    """
    if not phone:
        return None, None
    try:
        parsed = phonenumbers.parse(phone, "PY")
    except phonenumbers.NumberParseException:
        return None, "Teléfono inválido"
    if not phonenumbers.is_valid_number(parsed):
        return None, "Teléfono inválido"
    return phonenumbers.format_number(
        parsed, phonenumbers.PhoneNumberFormat.E164,
    ), None


def parse_phone(phone_str: str | None) -> dict:
    """Parse an E.164 phone string into components for the frontend dropdown.

    Returns dict with keys:
        country_code: str (e.g. "+34")
        national_number: str (e.g. "652716447")
        country: str (region code, e.g. "ES")
        country_name: str (e.g. "España")
        valid: bool
        known_prefix: bool (True if prefix is in our dropdown list)
    """
    if not phone_str or not phone_str.startswith("+"):
        return {
            "country_code": "+595",
            "national_number": "",
            "country": "PY",
            "country_name": "Paraguay",
            "valid": False,
            "known_prefix": True,
        }

    try:
        parsed = phonenumbers.parse(phone_str, None)
        cc = f"+{parsed.country_code}"
        national = str(parsed.national_number)
        region = phonenumbers.region_code_for_number(parsed) or ""
        region = _REGION_ALIASES.get(region, region)
        valid = phonenumbers.is_valid_number(parsed)

        # Find in our list by region first, then by code
        entry = _REGION_MAP.get(region) or _CODE_MAP.get(cc)
        if entry:
            return {
                "country_code": entry[0],
                "national_number": national,
                "country": entry[1],
                "country_name": entry[2],
                "valid": valid,
                "known_prefix": True,
            }
        # Unknown prefix — still parsed OK
        return {
            "country_code": cc,
            "national_number": national,
            "country": region,
            "country_name": region,
            "valid": valid,
            "known_prefix": False,
        }
    except phonenumbers.NumberParseException:
        return {
            "country_code": "",
            "national_number": phone_str,
            "country": "",
            "country_name": "",
            "valid": False,
            "known_prefix": False,
        }


def validate_phone(phone_str: str) -> tuple[bool, str]:
    """Validate an E.164 phone number.

    Returns (is_valid, error_message).
    """
    if not phone_str:
        return False, "Teléfono requerido"
    if not phone_str.startswith("+"):
        return False, "Debe empezar con + (formato E.164)"

    try:
        parsed = phonenumbers.parse(phone_str, None)
        if not phonenumbers.is_valid_number(parsed):
            return False, f"Número inválido para +{parsed.country_code}"
        return True, ""
    except phonenumbers.NumberParseException as e:
        return False, f"No se pudo parsear: {e}"


def build_e164(country_code: str, national_number: str) -> str | None:
    """Build E.164 from prefix + national number. Returns None if invalid."""
    clean = national_number.lstrip("0").replace(" ", "").replace("-", "")
    if not clean or not clean.isdigit():
        return None
    return f"{country_code}{clean}"

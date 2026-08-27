"""Tests for InfoCasas lead parser — pure logic, no DB access.

Covers all public functions in
``app.bot.services.infocasas.lead_parser``:

- :func:`normalize_phone`
- :func:`derive_name`
- :func:`parse_relative_date`
- :func:`extract_consulta_id`
- :func:`should_process_notification`
- :func:`select_zone`
- :func:`parse_lead`

Date assertions use a generous ``timedelta`` tolerance (±5 seconds) instead
of ``freezegun`` since that package is not in requirements.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.bot.services.infocasas.lead_parser import (
    ParsedLead,
    derive_name,
    extract_consulta_id,
    normalize_phone,
    parse_lead,
    parse_relative_date,
    select_zone,
    should_process_notification,
)
from app.tz import PYT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOLERANCE = timedelta(seconds=5)


def _approx_now() -> datetime:
    return datetime.now(timezone.utc)


def _assert_approx(actual: datetime, expected: datetime, tol: timedelta = _TOLERANCE) -> None:
    """Assert *actual* is within *tol* of *expected*."""
    diff = abs(actual - expected)
    assert diff <= tol, f"Datetime {actual!r} not within {tol} of {expected!r}"


# ---------------------------------------------------------------------------
# TestNormalizePhone
# ---------------------------------------------------------------------------


class TestNormalizePhone:
    """Phone normalization to E.164 (Paraguay-aware)."""

    def test_already_e164(self) -> None:
        assert normalize_phone("+595981234567") == "+595981234567"

    def test_py_mobile_without_country_code(self) -> None:
        assert normalize_phone("0981234567") == "+595981234567"

    def test_py_mobile_with_country_no_plus(self) -> None:
        assert normalize_phone("595981234567") == "+595981234567"

    def test_messy_local_number(self) -> None:
        # Spaces and dashes stripped; leading 0 replaced with +595
        assert normalize_phone("09 8123-4567") == "+595981234567"

    def test_with_plus_and_spaces(self) -> None:
        assert normalize_phone("+595 981 234 567") == "+595981234567"

    def test_empty_string(self) -> None:
        assert normalize_phone("") is None

    def test_none(self) -> None:
        assert normalize_phone(None) is None

    def test_too_short(self) -> None:
        assert normalize_phone("123") is None

    def test_international_argentina(self) -> None:
        # Non-PY E.164 numbers must be preserved as-is
        result = normalize_phone("+5491155667788")
        assert result == "+5491155667788"

    def test_international_usa(self) -> None:
        assert normalize_phone("+12025551234") == "+12025551234"

    def test_just_plus(self) -> None:
        assert normalize_phone("+") is None

    def test_whitespace_only(self) -> None:
        assert normalize_phone("   ") is None

    def test_py_mobile_ten_digit_with_country_no_plus(self) -> None:
        # 595 + 10 digits → +595XXXXXXXXXX
        assert normalize_phone("5959812345678") == "+5959812345678"

    def test_nine_digit_local_with_zero_too_short(self) -> None:
        # "0" + 8 digits = 9 chars → +595 + 8 digits = 12 chars total
        # E.164 minimum for Paraguay (+595 + 8) = 12 chars, below the 13-char guard → None
        assert normalize_phone("021123456") is None


class TestNormalizePhoneEdgeCases:
    """Additional edge-case coverage for normalize_phone."""

    def test_parentheses_and_dashes(self) -> None:
        # "(0981) 234-567" → "0981234567" → "+595981234567"
        assert normalize_phone("(0981) 234-567") == "+595981234567"

    def test_already_e164_argentina_long(self) -> None:
        result = normalize_phone("+5491122334455")
        assert result is not None
        assert result.startswith("+")


class TestNormalizePhoneInfoCasasMalformed:
    """InfoCasas API sends phone with intercalated local '0' prefix.

    The IC API delivers numbers in format 5950XXXXXXXXX (country code 595
    + the local number including its leading 0, e.g. 0975639930).  Without
    a specific fix, the regex ``^595\\d{9,10}$`` accepts this and produces
    the malformed E.164 ``+5950975639930`` instead of ``+595975639930``.

    Real cases from production DB (2026-04-14):
      - Cresencia Caballero: 5950975639930 → +595975639930
      - Alice Monges:        5950986613437 → +595986613437
    """

    def test_cresencia_caballero_ic_format(self) -> None:
        # Real case: InfoCasas sent 5950975639930, correct is +595975639930
        assert normalize_phone("5950975639930") == "+595975639930"

    def test_alice_monges_ic_format(self) -> None:
        # Real case: InfoCasas sent 5950986613437, correct is +595986613437
        assert normalize_phone("5950986613437") == "+595986613437"

    def test_generic_ic_mobile_format(self) -> None:
        # Generic IC malformed format 5950981234567 → +595981234567
        assert normalize_phone("5950981234567") == "+595981234567"

    def test_ic_format_does_not_produce_5950_prefix(self) -> None:
        # Regression: result must NOT start with +5950
        result = normalize_phone("5950975639930")
        assert result is not None
        assert not result.startswith("+5950"), (
            f"normalize_phone produced malformed {result!r} — "
            "InfoCasas '5950XXXXXXXXX' must strip the intercalated 0"
        )

    def test_legitimate_595_number_unchanged(self) -> None:
        # A legitimate 595XXXXXXXXX (without the extra 0) must remain correct
        assert normalize_phone("595975639930") == "+595975639930"


# ---------------------------------------------------------------------------
# TestDeriveName
# ---------------------------------------------------------------------------


class TestDeriveName:
    """Display-name derivation with email-prefix fallback."""

    def test_normal_name(self) -> None:
        assert derive_name("Nicole Cáceres", None) == "Nicole Cáceres"

    def test_name_with_email_ignored(self) -> None:
        # Non-empty name takes precedence
        assert derive_name("Juan Pérez", "juan@example.com") == "Juan Pérez"

    def test_empty_name_has_email(self) -> None:
        result = derive_name("", "nicole.caceres123@gmail.com")
        assert result == "Nicole Caceres (InfoCasas)"

    def test_none_name_has_email(self) -> None:
        result = derive_name(None, "nicole.caceres123@gmail.com")
        assert result == "Nicole Caceres (InfoCasas)"

    def test_sentinel_sin_nombre_has_email(self) -> None:
        result = derive_name("Sin nombre", "juan.perez@example.com")
        assert result == "Juan Perez (InfoCasas)"

    def test_no_name_no_email(self) -> None:
        assert derive_name(None, None) == "Sin nombre (InfoCasas)"

    def test_empty_name_empty_email(self) -> None:
        assert derive_name("", "") == "Sin nombre (InfoCasas)"

    def test_underscores_in_email(self) -> None:
        result = derive_name("", "juan_perez99@example.com")
        assert result == "Juan Perez (InfoCasas)"

    def test_dashes_in_email(self) -> None:
        result = derive_name(None, "maria-garcia@example.com")
        assert result == "Maria Garcia (InfoCasas)"

    def test_trailing_digits_stripped(self) -> None:
        result = derive_name("", "user123@example.com")
        assert result == "User (InfoCasas)"

    def test_whitespace_name_treated_as_empty(self) -> None:
        result = derive_name("   ", "ana@example.com")
        # "   ".strip() == "" → treated as empty
        assert result == "Ana (InfoCasas)"

    def test_email_no_at_sign(self) -> None:
        # Invalid email without @
        result = derive_name("", "notanemail")
        assert result == "Sin nombre (InfoCasas)"


# ---------------------------------------------------------------------------
# TestParseRelativeDate
# ---------------------------------------------------------------------------


class TestParseRelativeDate:
    """Relative and absolute date parsing."""

    def test_none_returns_now(self) -> None:
        result = parse_relative_date(None)
        _assert_approx(result, _approx_now())

    def test_empty_string_returns_now(self) -> None:
        result = parse_relative_date("")
        _assert_approx(result, _approx_now())

    def test_hace_5_minutos(self) -> None:
        result = parse_relative_date("hace 5 minutos")
        expected = _approx_now() - timedelta(minutes=5)
        _assert_approx(result, expected)

    def test_hace_1_minuto_singular(self) -> None:
        result = parse_relative_date("hace 1 minuto")
        expected = _approx_now() - timedelta(minutes=1)
        _assert_approx(result, expected)

    def test_hace_2_horas(self) -> None:
        result = parse_relative_date("hace 2 horas")
        expected = _approx_now() - timedelta(hours=2)
        _assert_approx(result, expected)

    def test_hace_1_hora_singular(self) -> None:
        result = parse_relative_date("hace 1 hora")
        expected = _approx_now() - timedelta(hours=1)
        _assert_approx(result, expected)

    def test_hace_1_dia(self) -> None:
        result = parse_relative_date("hace 1 día")
        expected = _approx_now() - timedelta(days=1)
        _assert_approx(result, expected)

    def test_hace_3_dias_no_accent(self) -> None:
        result = parse_relative_date("hace 3 dias")
        expected = _approx_now() - timedelta(days=3)
        _assert_approx(result, expected)

    def test_hace_2_semanas(self) -> None:
        result = parse_relative_date("hace 2 semanas")
        expected = _approx_now() - timedelta(weeks=2)
        _assert_approx(result, expected)

    def test_hace_1_semana_singular(self) -> None:
        result = parse_relative_date("hace 1 semana")
        expected = _approx_now() - timedelta(weeks=1)
        _assert_approx(result, expected)

    def test_absolute_iso_datetime(self) -> None:
        """La fecha sin huso es hora de Paraguay (UTC-3), no UTC.

        Antes del 2026-08-24 esto devolvía 14:30 UTC y `consulta_date` quedaba
        3 h en el pasado. El instante real de las 14:30 en Asunción es 17:30
        UTC. El esperado NO se escribe a mano con `hours=3`: se construye con
        el huso real, así que si algún día Paraguay vuelve a mover el reloj el
        test sigue diciendo la verdad.
        """
        result = parse_relative_date("2026-03-28 14:30:00")
        esperado = datetime(2026, 3, 28, 14, 30, 0, tzinfo=PYT).astimezone(
            timezone.utc
        )
        assert result == esperado
        # Y el número concreto, para que el test falle si el huso desaparece:
        assert result == datetime(2026, 3, 28, 17, 30, 0, tzinfo=timezone.utc)

    def test_absolute_iso_date_only(self) -> None:
        result = parse_relative_date("2026-03-28")
        assert result == datetime(2026, 3, 28, 0, 0, 0, tzinfo=PYT).astimezone(
            timezone.utc
        )
        assert result == datetime(2026, 3, 28, 3, 0, 0, tzinfo=timezone.utc)

    def test_absolute_con_huso_explicito_se_respeta(self) -> None:
        """Si InfoCasas alguna vez manda el offset, gana el que viene."""
        result = parse_relative_date("2026-03-28T14:30:00+00:00")
        assert result == datetime(2026, 3, 28, 14, 30, 0, tzinfo=timezone.utc)

    def test_dmy_format(self) -> None:
        result = parse_relative_date("28/03/2026")
        assert result == datetime(2026, 3, 28, tzinfo=PYT).astimezone(timezone.utc)
        assert result == datetime(2026, 3, 28, 3, 0, 0, tzinfo=timezone.utc)

    def test_absoluta_nunca_queda_en_el_pasado_del_piso_medido(self) -> None:
        """El piso medido en producción: con la etiqueta UTC, el desfase contra
        el momento en que el lead entró nunca bajaba de 180 minutos sobre 714
        leads. Con la etiqueta correcta ese piso desaparece.

        Simula el caso real 69554154: InfoCasas dijo "2026-08-22 21:07:03" y el
        poll creó el contacto a las 00:08:11 UTC del 23. Bien parseado, la
        consulta ocurrió 68 segundos antes de que la viéramos.
        """
        visto_en_utc = datetime(2026, 8, 23, 0, 8, 11, tzinfo=timezone.utc)
        result = parse_relative_date("2026-08-22 21:07:03")
        desfase_min = (visto_en_utc - result).total_seconds() / 60
        assert 0 <= desfase_min < 5, (
            f"desfase de {desfase_min:.1f} min — con el huso mal etiquetado "
            "daban 181"
        )

    def test_unknown_format_returns_now(self) -> None:
        result = parse_relative_date("last tuesday")
        _assert_approx(result, _approx_now())

    def test_result_is_utc_aware(self) -> None:
        result = parse_relative_date("hace 10 minutos")
        assert result.tzinfo is not None
        assert result.tzinfo == timezone.utc

    def test_absolute_result_is_utc_aware(self) -> None:
        result = parse_relative_date("2026-01-01 00:00:00")
        assert result.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# TestExtractConsultaId
# ---------------------------------------------------------------------------


class TestExtractConsultaId:
    """Extraction of consulta_id from notification URLs."""

    def test_full_url_with_id(self) -> None:
        url = "/sitio/index.php?mid=consultas&id=66065340"
        assert extract_consulta_id(url) == "66065340"

    def test_query_string_id_first(self) -> None:
        url = "/page?id=12345&other=foo"
        assert extract_consulta_id(url) == "12345"

    def test_url_without_id(self) -> None:
        assert extract_consulta_id("/sitio/index.php?mid=consultas") is None

    def test_none(self) -> None:
        assert extract_consulta_id(None) is None

    def test_empty_string(self) -> None:
        assert extract_consulta_id("") is None

    def test_id_is_string(self) -> None:
        result = extract_consulta_id("/page?id=99999")
        assert isinstance(result, str)
        assert result == "99999"

    def test_id_with_hash_fragment(self) -> None:
        url = "/page?id=777#section"
        assert extract_consulta_id(url) == "777"


# ---------------------------------------------------------------------------
# TestShouldProcessNotification
# ---------------------------------------------------------------------------


class TestShouldProcessNotification:
    """Notification filtering logic."""

    def test_unseen_notification_processed(self) -> None:
        notification = {"seen": False, "created_at": "28/03/2026"}
        assert should_process_notification(notification) is True

    def test_unseen_false_string_treated_as_unseen(self) -> None:
        # seen=False → process regardless of date
        notification = {"seen": False, "created_at": "2026-03-01 10:00:00"}
        assert should_process_notification(notification) is True

    def test_seen_with_relative_date_processed(self) -> None:
        notification = {"seen": True, "created_at": "hace 5 horas"}
        assert should_process_notification(notification) is True

    def test_seen_with_hace_1_dia_processed(self) -> None:
        notification = {"seen": True, "created_at": "hace 1 día"}
        assert should_process_notification(notification) is True

    def test_seen_with_absolute_dmy_skipped(self) -> None:
        notification = {"seen": True, "created_at": "28/03/2026"}
        assert should_process_notification(notification) is False

    def test_seen_with_absolute_iso_skipped(self) -> None:
        notification = {"seen": True, "created_at": "2026-03-28 14:30:00"}
        assert should_process_notification(notification) is False

    def test_seen_with_iso_date_only_skipped(self) -> None:
        notification = {"seen": True, "created_at": "2026-03-28"}
        assert should_process_notification(notification) is False

    def test_missing_seen_key_treated_as_unseen(self) -> None:
        # No 'seen' key → defaults to False → process
        notification = {"created_at": "2026-03-28 14:30:00"}
        assert should_process_notification(notification) is True

    def test_seen_empty_created_at_processed(self) -> None:
        # Seen but no date → treat as relative (empty → no absolute date)
        notification = {"seen": True, "created_at": ""}
        assert should_process_notification(notification) is False

    def test_seen_none_created_at_skipped(self) -> None:
        notification = {"seen": True, "created_at": None}
        assert should_process_notification(notification) is False


# ---------------------------------------------------------------------------
# TestSelectZone
# ---------------------------------------------------------------------------


class TestSelectZone:
    """Zone string selection for WhatsApp welcome template."""

    def test_matched_property_city_takes_priority(self) -> None:
        prop = {"city": "Asuncion", "title": "Casa amplia"}
        assert select_zone(prop, "Fernando de la Mora", "Casa en Alquiler...") == "Asuncion"

    def test_listing_city_when_no_property(self) -> None:
        assert select_zone(None, "Fernando de la Mora", "Casa en Alquiler...") == "Fernando de la Mora"

    def test_listing_city_when_property_has_no_city(self) -> None:
        prop = {"city": None}
        assert select_zone(prop, "Luque", "Depto en venta") == "Luque"

    def test_listing_city_when_property_city_empty(self) -> None:
        prop = {"city": ""}
        assert select_zone(prop, "Lambare", "Depto") == "Lambare"

    def test_listing_title_truncated_when_no_city(self) -> None:
        long_title = "Casa en Alquiler en Barrio Residencial Las Palmas"
        result = select_zone(None, None, long_title)
        assert result == long_title[:30]
        assert len(result) == 30

    def test_listing_title_short_not_truncated(self) -> None:
        short_title = "Depto céntrico"
        result = select_zone(None, None, short_title)
        assert result == short_title

    def test_fallback_tu_zona(self) -> None:
        assert select_zone(None, None, None) == "tu zona"

    def test_fallback_with_empty_strings(self) -> None:
        assert select_zone(None, "", "") == "tu zona"

    def test_no_property_at_all(self) -> None:
        assert select_zone(None, None, "") == "tu zona"


# ---------------------------------------------------------------------------
# TestParseLead
# ---------------------------------------------------------------------------

_FULL_LEAD_DATA: dict = {
    "id": "66065340",
    "message": "Hola, me interesa la propiedad.",
    "created_at": "2026-03-28 14:30:00",
    "from": {
        "name": "Nicole Cáceres",
        "email": "nicole@example.com",
        "phone": "+595900000001",
        "whatsapp_phone": None,
        "has_whatsapp": False,
    },
    "listing": {
        "id": "193572330",
        "title": "Casa en Alquiler en Fernando de la Mora",
        "code": "OF23CE",
        "neighborhood": {"name": "Fernando de la Mora"},
    },
}


class TestParseLead:
    """Full lead parsing from raw GraphQL response."""

    def test_full_data_returns_parsed_lead(self) -> None:
        result = parse_lead(_FULL_LEAD_DATA)
        assert isinstance(result, ParsedLead)

    def test_consulta_id_extracted(self) -> None:
        result = parse_lead(_FULL_LEAD_DATA)
        assert result is not None
        assert result.consulta_id == "66065340"

    def test_name_preserved(self) -> None:
        result = parse_lead(_FULL_LEAD_DATA)
        assert result is not None
        assert result.name == "Nicole Cáceres"

    def test_phone_normalized(self) -> None:
        result = parse_lead(_FULL_LEAD_DATA)
        assert result is not None
        assert result.phone == "+595900000001"

    def test_email_set(self) -> None:
        result = parse_lead(_FULL_LEAD_DATA)
        assert result is not None
        assert result.email == "nicole@example.com"

    def test_message_set(self) -> None:
        result = parse_lead(_FULL_LEAD_DATA)
        assert result is not None
        assert result.message == "Hola, me interesa la propiedad."

    def test_property_code_set(self) -> None:
        result = parse_lead(_FULL_LEAD_DATA)
        assert result is not None
        assert result.property_code == "OF23CE"

    def test_property_title_set(self) -> None:
        result = parse_lead(_FULL_LEAD_DATA)
        assert result is not None
        assert result.property_title == "Casa en Alquiler en Fernando de la Mora"

    def test_listing_city_set(self) -> None:
        result = parse_lead(_FULL_LEAD_DATA)
        assert result is not None
        assert result.listing_city == "Fernando de la Mora"

    def test_has_whatsapp_false(self) -> None:
        result = parse_lead(_FULL_LEAD_DATA)
        assert result is not None
        assert result.has_whatsapp is False

    def test_consulta_date_parsed_utc(self) -> None:
        """El campo sale UTC-aware, pero interpretando el string como hora de
        Paraguay: "2026-03-28 14:30:00" son las 17:30 UTC. Este test decia
        14:30 y fijaba el bug —`consulta_date` tres horas en el pasado— que
        hacia que el panel dijera «hace 3 h» a un lead recien entrado."""
        result = parse_lead(_FULL_LEAD_DATA)
        assert result is not None
        assert result.consulta_date.tzinfo == timezone.utc
        assert result.consulta_date == datetime(
            2026, 3, 28, 14, 30, 0, tzinfo=PYT
        ).astimezone(timezone.utc)
        assert result.consulta_date == datetime(
            2026, 3, 28, 17, 30, 0, tzinfo=timezone.utc
        )

    def test_no_phone_has_email_returns_lead(self) -> None:
        data = {
            **_FULL_LEAD_DATA,
            "from": {
                "name": "Ana",
                "email": "ana@example.com",
                "phone": None,
                "whatsapp_phone": None,
                "has_whatsapp": False,
            },
        }
        result = parse_lead(data)
        assert result is not None
        assert result.phone is None
        assert result.email == "ana@example.com"

    def test_no_phone_no_email_returns_none(self) -> None:
        data = {
            **_FULL_LEAD_DATA,
            "from": {
                "name": "Unknown",
                "email": None,
                "phone": None,
                "whatsapp_phone": None,
                "has_whatsapp": False,
            },
        }
        result = parse_lead(data)
        assert result is None

    def test_whatsapp_phone_preferred_when_has_whatsapp(self) -> None:
        data = {
            **_FULL_LEAD_DATA,
            "from": {
                "name": "Pedro",
                "email": "pedro@example.com",
                "phone": "+595991111111",
                "whatsapp_phone": "+595992222222",
                "has_whatsapp": True,
            },
        }
        result = parse_lead(data)
        assert result is not None
        assert result.phone == "+595992222222"

    def test_phone_used_when_has_whatsapp_false(self) -> None:
        data = {
            **_FULL_LEAD_DATA,
            "from": {
                "name": "Maria",
                "email": None,
                "phone": "+595993333333",
                "whatsapp_phone": "+595994444444",
                "has_whatsapp": False,
            },
        }
        result = parse_lead(data)
        assert result is not None
        assert result.phone == "+595993333333"

    def test_empty_name_derived_from_email(self) -> None:
        data = {
            **_FULL_LEAD_DATA,
            "from": {
                "name": "",
                "email": "carlos.romero99@gmail.com",
                "phone": "+595981234567",
                "whatsapp_phone": None,
                "has_whatsapp": False,
            },
        }
        result = parse_lead(data)
        assert result is not None
        assert result.name == "Carlos Romero (InfoCasas)"

    def test_missing_id_returns_none(self) -> None:
        data = {k: v for k, v in _FULL_LEAD_DATA.items() if k != "id"}
        result = parse_lead(data)
        assert result is None

    def test_missing_listing_section(self) -> None:
        data = {k: v for k, v in _FULL_LEAD_DATA.items() if k != "listing"}
        result = parse_lead(data)
        assert result is not None
        assert result.property_code is None
        assert result.property_title is None
        assert result.listing_city is None

    def test_empty_message_is_none(self) -> None:
        data = {**_FULL_LEAD_DATA, "message": ""}
        result = parse_lead(data)
        assert result is not None
        assert result.message is None

    def test_relative_date_in_lead(self) -> None:
        data = {**_FULL_LEAD_DATA, "created_at": "hace 30 minutos"}
        result = parse_lead(data)
        assert result is not None
        expected = _approx_now() - timedelta(minutes=30)
        _assert_approx(result.consulta_date, expected)

    def test_has_whatsapp_true(self) -> None:
        data = {
            **_FULL_LEAD_DATA,
            "from": {
                **_FULL_LEAD_DATA["from"],
                "has_whatsapp": True,
                "whatsapp_phone": "+595995555555",
            },
        }
        result = parse_lead(data)
        assert result is not None
        assert result.has_whatsapp is True

    def test_empty_phone_and_valid_email_returns_lead(self) -> None:
        data = {
            **_FULL_LEAD_DATA,
            "from": {
                "name": "Luis",
                "email": "luis@mail.com",
                "phone": "",
                "whatsapp_phone": "",
                "has_whatsapp": False,
            },
        }
        result = parse_lead(data)
        assert result is not None
        assert result.phone is None
        assert result.email == "luis@mail.com"


# ---------------------------------------------------------------------------
# TestParsedLeadIsReassigned
# ---------------------------------------------------------------------------


class TestParsedLeadIsReassigned:
    """Tests for is_reassigned detection in parse_lead()."""

    def _base_data(self, message: str | None = None) -> dict:
        return {
            "id": "66065340",
            "message": message,
            "created_at": "2026-03-28 14:30:00",
            "from": {
                "name": "Test Lead",
                "email": "test@example.com",
                "phone": "+595900000001",
                "whatsapp_phone": None,
                "has_whatsapp": False,
            },
            "listing": {
                "id": "193572330",
                "title": "Casa en Fernando de la Mora",
                "code": "OF23CE",
                "neighborhood": {"name": "Fernando de la Mora"},
            },
        }

    def test_not_reassigned_when_no_message(self) -> None:
        data = self._base_data(message=None)
        result = parse_lead(data)
        assert result is not None
        assert result.is_reassigned is False

    def test_not_reassigned_normal_message(self) -> None:
        data = self._base_data(message="Me interesa la propiedad, quiero más info.")
        result = parse_lead(data)
        assert result is not None
        assert result.is_reassigned is False

    def test_reassigned_exact(self) -> None:
        data = self._base_data(message="Consulta reenviada desde InfoCasas.")
        result = parse_lead(data)
        assert result is not None
        assert result.is_reassigned is True

    def test_reassigned_case_insensitive(self) -> None:
        data = self._base_data(message="CONSULTA REENVIADA por sistema automatico.")
        result = parse_lead(data)
        assert result is not None
        assert result.is_reassigned is True

    def test_reassigned_mixed_case(self) -> None:
        data = self._base_data(message="Esta es una Consulta Reenviada de otra inmobiliaria.")
        result = parse_lead(data)
        assert result is not None
        assert result.is_reassigned is True

    def test_reassigned_extra_whitespace(self) -> None:
        data = self._base_data(message="consulta  reenviada con espacios extra.")
        result = parse_lead(data)
        assert result is not None
        assert result.is_reassigned is True

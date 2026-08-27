"""
Tests for app/utils/phone_utils.py

Covers:
- PREFIXES list integrity (no duplicates, format)
- parse_phone() with known prefixes, unknown prefixes, edge cases
- validate_phone() valid/invalid numbers
- build_e164() construction and edge cases
- _REGION_ALIASES mapping for territories
"""
import pytest
from app.utils.phone_utils import (
    PREFIXES, parse_phone, validate_phone, build_e164,
    _REGION_MAP, _CODE_MAP, _REGION_ALIASES,
)


# ---------------------------------------------------------------------------
# PREFIXES list integrity
# ---------------------------------------------------------------------------

class TestPrefixesIntegrity:
    def test_prefixes_not_empty(self):
        assert len(PREFIXES) > 50

    def test_first_entry_is_paraguay(self):
        assert PREFIXES[0] == ("+595", "PY", "Paraguay")

    def test_all_entries_are_tuples_of_three(self):
        for entry in PREFIXES:
            assert isinstance(entry, tuple), f"Not a tuple: {entry}"
            assert len(entry) == 3, f"Wrong length: {entry}"

    def test_all_codes_start_with_plus(self):
        for code, region, name in PREFIXES:
            assert code.startswith("+"), f"Code missing +: {code} ({region})"
            assert code[1:].isdigit(), f"Non-numeric code: {code} ({region})"

    def test_all_regions_are_two_letter(self):
        for code, region, name in PREFIXES:
            assert len(region) == 2, f"Region not 2 chars: {region}"
            assert region == region.upper(), f"Region not uppercase: {region}"

    def test_no_duplicate_region_codes(self):
        """Each region code should appear only once in the list."""
        regions = [r for _, r, _ in PREFIXES]
        duplicates = [r for r in regions if regions.count(r) > 1]
        assert not duplicates, f"Duplicate regions: {set(duplicates)}"

    def test_region_map_matches_prefixes(self):
        for code, region, name in PREFIXES:
            assert region in _REGION_MAP
            assert _REGION_MAP[region] == (code, region, name)

    def test_all_names_non_empty(self):
        for code, region, name in PREFIXES:
            assert name.strip(), f"Empty name for {region}"


# ---------------------------------------------------------------------------
# LATAM coverage
# ---------------------------------------------------------------------------

class TestLatamCoverage:
    LATAM_REGIONS = [
        "PY", "AR", "BR", "UY", "CL", "CO", "PE", "BO", "EC", "VE",
        "MX", "CR", "PA", "GT", "HN", "SV", "NI", "CU", "DO", "PR",
        "HT", "JM", "TT", "GY", "SR", "BZ",
    ]

    def test_all_latam_countries_present(self):
        regions_in_list = {r for _, r, _ in PREFIXES}
        for region in self.LATAM_REGIONS:
            assert region in regions_in_list, f"Missing LATAM country: {region}"


# ---------------------------------------------------------------------------
# Europe coverage
# ---------------------------------------------------------------------------

class TestEuropeCoverage:
    EUROPE_REGIONS = [
        "ES", "PT", "FR", "IT", "DE", "GB", "NL", "BE", "CH", "AT",
        "SE", "NO", "DK", "FI", "IE", "PL", "CZ", "SK", "RO", "HU",
        "GR", "HR", "RS", "BG", "SI", "EE", "LV", "LT", "UA", "BY",
        "RU", "TR", "IS", "LU", "MT", "CY", "AL", "BA", "ME", "MK",
        "MD", "XK",
    ]

    def test_all_europe_countries_present(self):
        regions_in_list = {r for _, r, _ in PREFIXES}
        for region in self.EUROPE_REGIONS:
            assert region in regions_in_list, f"Missing Europe country: {region}"


# ---------------------------------------------------------------------------
# parse_phone() — known prefixes
# ---------------------------------------------------------------------------

class TestParsePhoneKnown:
    def test_paraguay_number(self):
        result = parse_phone("+595981555123")
        assert result["country"] == "PY"
        assert result["country_code"] == "+595"
        assert result["national_number"] == "981555123"
        assert result["country_name"] == "Paraguay"
        assert result["valid"] is True
        assert result["known_prefix"] is True

    def test_argentina_number(self):
        result = parse_phone("+5491155551234")
        assert result["country"] == "AR"
        assert result["country_code"] == "+54"
        assert result["known_prefix"] is True

    def test_brasil_number(self):
        result = parse_phone("+5511987654321")
        assert result["country"] == "BR"
        assert result["country_code"] == "+55"
        assert result["known_prefix"] is True

    def test_usa_number(self):
        result = parse_phone("+12025551234")
        assert result["country"] == "US"
        assert result["country_code"] == "+1"
        assert result["known_prefix"] is True

    def test_spain_number(self):
        result = parse_phone("+34652716447")
        assert result["country"] == "ES"
        assert result["country_code"] == "+34"
        assert result["known_prefix"] is True

    def test_uk_number(self):
        """UK mobile — phonenumbers may return GG/JE but alias maps to GB."""
        result = parse_phone("+447911123456")
        assert result["country"] == "GB"
        assert result["country_code"] == "+44"
        assert result["country_name"] == "Reino Unido"
        assert result["known_prefix"] is True

    def test_uk_landline(self):
        result = parse_phone("+442071234567")
        assert result["country"] == "GB"
        assert result["country_code"] == "+44"

    def test_india_number(self):
        result = parse_phone("+919876543210")
        assert result["country"] == "IN"
        assert result["country_code"] == "+91"
        assert result["known_prefix"] is True

    def test_korea_number(self):
        result = parse_phone("+821055551234")
        assert result["country"] == "KR"
        assert result["country_code"] == "+82"
        assert result["known_prefix"] is True

    def test_japan_number(self):
        result = parse_phone("+81312345678")
        assert result["country"] == "JP"
        assert result["country_code"] == "+81"
        assert result["known_prefix"] is True

    def test_australia_number(self):
        result = parse_phone("+61412345678")
        assert result["country"] == "AU"
        assert result["country_code"] == "+61"
        assert result["known_prefix"] is True

    def test_russia_number(self):
        result = parse_phone("+79161234567")
        assert result["country"] == "RU"
        assert result["country_code"] == "+7"
        assert result["known_prefix"] is True


# ---------------------------------------------------------------------------
# parse_phone() — region aliases (territories → parent)
# ---------------------------------------------------------------------------

class TestParsePhoneAliases:
    def test_guernsey_maps_to_gb(self):
        """GG region alias should resolve to GB."""
        assert _REGION_ALIASES.get("GG") == "GB"

    def test_jersey_maps_to_gb(self):
        assert _REGION_ALIASES.get("JE") == "GB"

    def test_isle_of_man_maps_to_gb(self):
        assert _REGION_ALIASES.get("IM") == "GB"

    def test_christmas_island_maps_to_au(self):
        assert _REGION_ALIASES.get("CX") == "AU"

    def test_french_territories_map_to_fr(self):
        french = ["BL", "MF", "GP", "MQ", "GF", "RE", "YT", "PM", "WF", "NC"]
        for territory in french:
            assert _REGION_ALIASES.get(territory) == "FR", f"{territory} should map to FR"

    def test_dutch_caribbean_maps_to_nl(self):
        dutch = ["BQ", "CW", "SX"]
        for territory in dutch:
            assert _REGION_ALIASES.get(territory) == "NL", f"{territory} should map to NL"

    def test_non_aliased_region_unchanged(self):
        """A normal region should not be in aliases."""
        assert "PY" not in _REGION_ALIASES
        assert "US" not in _REGION_ALIASES
        assert "ES" not in _REGION_ALIASES


# ---------------------------------------------------------------------------
# parse_phone() — edge cases
# ---------------------------------------------------------------------------

class TestParsePhoneEdgeCases:
    def test_none_returns_default_py(self):
        result = parse_phone(None)
        assert result["country"] == "PY"
        assert result["country_code"] == "+595"
        assert result["valid"] is False
        assert result["known_prefix"] is True

    def test_empty_string_returns_default_py(self):
        result = parse_phone("")
        assert result["country"] == "PY"
        assert result["valid"] is False

    def test_no_plus_prefix_returns_default_py(self):
        result = parse_phone("595981555123")
        assert result["country"] == "PY"
        assert result["valid"] is False

    def test_invalid_number_returns_parse_error(self):
        result = parse_phone("+abc")
        assert result["valid"] is False
        assert result["known_prefix"] is False

    def test_short_number_invalid(self):
        result = parse_phone("+59512")
        assert result["valid"] is False

    def test_all_result_keys_present(self):
        """Every result should contain all 6 keys."""
        required = {"country_code", "national_number", "country", "country_name", "valid", "known_prefix"}
        for phone in [None, "", "+595981555123", "+12025551234", "+abc"]:
            result = parse_phone(phone)
            assert required == set(result.keys()), f"Missing keys for {phone}: {required - set(result.keys())}"


# ---------------------------------------------------------------------------
# validate_phone()
# ---------------------------------------------------------------------------

class TestValidatePhone:
    def test_valid_py_number(self):
        valid, err = validate_phone("+595981555123")
        assert valid is True
        assert err == ""

    def test_valid_us_number(self):
        valid, err = validate_phone("+12025551234")
        assert valid is True

    def test_valid_es_number(self):
        valid, err = validate_phone("+34652716447")
        assert valid is True

    def test_empty_string(self):
        valid, err = validate_phone("")
        assert valid is False
        assert "requerido" in err.lower()

    def test_no_plus_prefix(self):
        valid, err = validate_phone("595981555123")
        assert valid is False
        assert "+" in err

    def test_invalid_number_for_country(self):
        valid, err = validate_phone("+595123")
        assert valid is False
        assert "inválido" in err.lower() or "parsear" in err.lower()

    def test_garbage_input(self):
        valid, err = validate_phone("+not_a_number")
        assert valid is False


# ---------------------------------------------------------------------------
# build_e164()
# ---------------------------------------------------------------------------

class TestBuildE164:
    def test_basic_build(self):
        assert build_e164("+595", "981555123") == "+595981555123"

    def test_strips_leading_zeros(self):
        assert build_e164("+595", "0981555123") == "+595981555123"

    def test_strips_spaces_and_dashes(self):
        assert build_e164("+595", "981 555-123") == "+595981555123"

    def test_strips_multiple_leading_zeros(self):
        assert build_e164("+44", "007911123456") == "+447911123456"

    def test_empty_national_returns_none(self):
        assert build_e164("+595", "") is None

    def test_non_digit_only_returns_none(self):
        assert build_e164("+595", "abc") is None

    def test_spaces_only_returns_none(self):
        assert build_e164("+595", "   ") is None

    def test_various_prefixes(self):
        assert build_e164("+1", "2025551234") == "+12025551234"
        assert build_e164("+34", "652716447") == "+34652716447"
        assert build_e164("+91", "9876543210") == "+919876543210"

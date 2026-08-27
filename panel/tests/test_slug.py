"""Tests for panel.app.utils.slug.slugify."""

import pytest

from app.utils.slug import slugify


class TestSlugifyAccents:
    def test_accents_and_enie(self) -> None:
        assert slugify("Ñandutí Ítá") == "nanduti-ita"

    def test_mixed_accents(self) -> None:
        assert slugify("Café Ñoño") == "cafe-nono"


class TestSlugifySigns:
    def test_exclamation_and_em_dash(self) -> None:
        assert slugify("¡OPORTUNIDAD! – Casa 3D.") == "oportunidad-casa-3d"

    def test_em_dash_collapses_to_single_hyphen(self) -> None:
        # em-dash between words should produce exactly one hyphen
        assert slugify("alpha–beta") == "alpha-beta"

    def test_multiple_hyphens_collapse(self) -> None:
        assert slugify("a---b") == "a-b"


class TestSlugifyWhitespace:
    def test_multiple_spaces_collapse(self) -> None:
        assert slugify("casa   grande") == "casa-grande"

    def test_leading_trailing_spaces(self) -> None:
        assert slugify("  terreno  ") == "terreno"


class TestSlugifyTruncation:
    def test_truncates_to_max_len(self) -> None:
        long_text = "a" * 100
        result = slugify(long_text, max_len=80)
        assert len(result) == 80

    def test_no_trailing_hyphen_after_truncation(self) -> None:
        # Build a string whose slug would be cut right at a hyphen boundary
        # "aaa-bbb-ccc..." truncated at a position that lands on "-"
        text = "aaa bbb ccc ddd eee fff ggg hhh iii jjj kkk lll mmm nnn"
        result = slugify(text, max_len=7)
        assert not result.endswith("-")

    def test_custom_max_len(self) -> None:
        result = slugify("Terreno en Asuncion Paraguay", max_len=10)
        assert len(result) <= 10
        assert not result.endswith("-")


class TestSlugifyFallback:
    def test_none_returns_fallback(self) -> None:
        assert slugify(None) == "propiedad"

    def test_empty_string_returns_fallback(self) -> None:
        assert slugify("") == "propiedad"

    def test_only_special_chars_returns_fallback(self) -> None:
        assert slugify("¡¡¡") == "propiedad"

    def test_only_punctuation_returns_fallback(self) -> None:
        assert slugify("!@#$%") == "propiedad"


class TestSlugifyCase:
    def test_uppercase_lowercased(self) -> None:
        assert slugify("EN VENTA CASA") == "en-venta-casa"

    def test_mixed_case(self) -> None:
        assert slugify("Casa En Venta") == "casa-en-venta"


class TestSlugifyRealTitles:
    def test_real_title_with_punctuation(self) -> None:
        result = slugify("¡Terreno Estratégico en Obligado, Itapúa!")
        assert result == "terreno-estrategico-en-obligado-itapua"

    def test_real_title_mixed_signs(self) -> None:
        result = slugify(
            "EN VENTA – PROPIEDAD MIXTA SOBRE ASFALTO EN J. AUGUSTO SALDÍVAR"
        )
        assert result == "en-venta-propiedad-mixta-sobre-asfalto-en-j-augusto-saldivar"

"""Tests for tz.py timezone utilities."""
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.tz import wa_timestamp, to_pyt, humandate, strip_markdown, PYT, _DAY_ES

# Fixed "now" for all tests: Thursday 2026-04-02 15:30:00 PYT
FIXED_NOW = datetime(2026, 4, 2, 15, 30, 0, tzinfo=PYT)


class TestWaTimestamp:
    """Tests for the wa_timestamp Jinja2 filter."""

    def test_none_returns_dash(self):
        assert wa_timestamp(None, now=FIXED_NOW) == "\u2014"

    def test_today_shows_time(self):
        dt = datetime(2026, 4, 2, 9, 15, tzinfo=PYT)
        assert wa_timestamp(dt, now=FIXED_NOW) == "09:15"

    def test_today_midnight(self):
        dt = datetime(2026, 4, 2, 0, 0, tzinfo=PYT)
        assert wa_timestamp(dt, now=FIXED_NOW) == "00:00"

    def test_today_end_of_day(self):
        dt = datetime(2026, 4, 2, 23, 59, tzinfo=PYT)
        assert wa_timestamp(dt, now=FIXED_NOW) == "23:59"

    def test_yesterday(self):
        dt = datetime(2026, 4, 1, 14, 0, tzinfo=PYT)
        assert wa_timestamp(dt, now=FIXED_NOW) == "Ayer"

    def test_two_days_ago(self):
        # 2026-03-31 is a Tuesday (weekday=1)
        dt = datetime(2026, 3, 31, 10, 0, tzinfo=PYT)
        assert wa_timestamp(dt, now=FIXED_NOW) == "Martes"

    def test_six_days_ago_boundary(self):
        # 2026-03-27 is a Friday (weekday=4)
        dt = datetime(2026, 3, 27, 8, 0, tzinfo=PYT)
        assert wa_timestamp(dt, now=FIXED_NOW) == "Viernes"

    def test_seven_days_ago_shows_date(self):
        # delta=7, same year -> DD/MM
        dt = datetime(2026, 3, 26, 12, 0, tzinfo=PYT)
        assert wa_timestamp(dt, now=FIXED_NOW) == "26/03"

    def test_same_year_older(self):
        dt = datetime(2026, 1, 15, 10, 0, tzinfo=PYT)
        assert wa_timestamp(dt, now=FIXED_NOW) == "15/01"

    def test_different_year(self):
        dt = datetime(2025, 12, 25, 10, 0, tzinfo=PYT)
        assert wa_timestamp(dt, now=FIXED_NOW) == "25/12/25"

    def test_utc_aware_converts_to_pyt(self):
        # 2026-04-02 20:45 UTC = 2026-04-02 17:45 PYT (same day)
        dt = datetime(2026, 4, 2, 20, 45, tzinfo=timezone.utc)
        assert wa_timestamp(dt, now=FIXED_NOW) == "17:45"

    def test_utc_date_boundary_yesterday_in_pyt(self):
        # 2026-04-02 01:00 UTC = 2026-04-01 22:00 PYT (yesterday)
        dt = datetime(2026, 4, 2, 1, 0, tzinfo=timezone.utc)
        assert wa_timestamp(dt, now=FIXED_NOW) == "Ayer"

    def test_naive_datetime_treated_as_utc(self):
        # 2026-04-02 02:00 naive -> treated as UTC -> 2026-04-01 23:00 PYT -> Ayer
        dt = datetime(2026, 4, 2, 2, 0)
        assert wa_timestamp(dt, now=FIXED_NOW) == "Ayer"

    def test_future_date_shows_time(self):
        # tomorrow in PYT -> delta < 0 -> treated as today, show time
        dt = datetime(2026, 4, 3, 10, 0, tzinfo=PYT)
        assert wa_timestamp(dt, now=FIXED_NOW) == "10:00"

    def test_midnight_boundary_yesterday_2359(self):
        now = datetime(2026, 4, 2, 0, 0, 0, tzinfo=PYT)
        dt = datetime(2026, 4, 1, 23, 59, 59, tzinfo=PYT)
        assert wa_timestamp(dt, now=now) == "Ayer"

    def test_midnight_boundary_today_0000(self):
        now = datetime(2026, 4, 2, 0, 0, 1, tzinfo=PYT)
        dt = datetime(2026, 4, 2, 0, 0, 0, tzinfo=PYT)
        assert wa_timestamp(dt, now=now) == "00:00"


class TestWaTimestampAllDayNames:
    """Parametrized tests to verify all 7 Spanish day names are reachable."""

    @pytest.mark.parametrize("weekday,expected", [
        (0, "Lunes"),
        (1, "Martes"),
        (2, "Miercoles"),
        (3, "Jueves"),
        (4, "Viernes"),
        (5, "Sabado"),
        (6, "Domingo"),
    ])
    def test_day_name_for_weekday(self, weekday, expected):
        """For each weekday, construct a dt that lands 2-4 days before now."""
        from datetime import timedelta
        # Find a Monday (weekday=0) base to anchor all offsets
        # 2026-04-06 is a Monday (verified: Apr 2 Thu, +4 = Apr 6 Mon)
        monday = datetime(2026, 4, 6, 10, 0, tzinfo=PYT)
        target = monday + timedelta(days=weekday)
        # now must be 2-6 days after target
        test_now = target + timedelta(days=3)
        assert wa_timestamp(target, now=test_now) == expected


class TestDayEsDict:
    """Tests for the _DAY_ES day name dictionary."""

    def test_all_seven_days_present(self):
        assert set(_DAY_ES.keys()) == {0, 1, 2, 3, 4, 5, 6}

    def test_all_values_capitalized(self):
        for day_name in _DAY_ES.values():
            assert day_name[0].isupper(), f"{day_name!r} should start with uppercase"


class TestToPyt:
    """Tests for the to_pyt Jinja2 filter."""

    def test_none_returns_dash(self):
        assert to_pyt(None) == "\u2014"

    def test_default_format(self):
        dt = datetime(2026, 1, 15, 18, 0, tzinfo=timezone.utc)
        result = to_pyt(dt)
        # UTC 18:00 = PYT 15:00 (UTC-3)
        assert "15:00" in result

    def test_custom_format(self):
        dt = datetime(2026, 1, 15, 18, 0, tzinfo=timezone.utc)
        result = to_pyt(dt, "%H:%M")
        assert result == "15:00"


class TestHumandate:
    """Tests for the humandate Jinja2 filter."""

    def test_none_returns_dash(self):
        assert humandate(None) == "\u2014"

    def test_same_year_omits_year(self):
        # Jan 15 of some year -- we check format not exact year
        from datetime import date
        import datetime as dt_module
        today = dt_module.datetime.now(PYT).date()
        d = date(today.year, 1, 15)
        result = humandate(d)
        assert result == "15 ene"

    def test_different_year_includes_year(self):
        from datetime import date
        d = date(2025, 12, 25)
        result = humandate(d)
        assert "25" in result and "dic" in result and "2025" in result


class TestStripMarkdown:
    """Tests for the strip_markdown Jinja2 filter."""

    # --- Edge cases ---

    def test_none_returns_none(self):
        assert strip_markdown(None) is None  # type: ignore[arg-type]

    def test_empty_string_returns_empty(self):
        assert strip_markdown("") == ""

    def test_plain_text_unchanged(self):
        assert strip_markdown("Hola mundo") == "Hola mundo"

    def test_unicode_and_emoji_preserved(self):
        assert strip_markdown("Casa en Lambaré 🏡 con jardín") == "Casa en Lambaré 🏡 con jardín"

    def test_accented_chars_preserved(self):
        assert strip_markdown("habitación con ventilación") == "habitación con ventilación"

    # --- WhatsApp bold (*bold*) ---

    def test_wa_single_asterisk_bold(self):
        assert strip_markdown("*Casa en Lambaré*") == "Casa en Lambaré"

    def test_wa_bold_mid_sentence(self):
        assert strip_markdown("Precio: *USD 150.000*") == "Precio: USD 150.000"

    # --- Standard Markdown bold (**bold**) ---

    def test_double_asterisk_bold(self):
        assert strip_markdown("**Casa en Lambaré**") == "Casa en Lambaré"

    def test_double_asterisk_bold_mid_sentence(self):
        assert strip_markdown("Tipo: **Departamento**") == "Tipo: Departamento"

    # --- Italic (_italic_ and *italic* after bold is stripped) ---

    def test_underscore_italic(self):
        assert strip_markdown("_3 dormitorios_") == "3 dormitorios"

    def test_underscore_italic_mid_sentence(self):
        assert strip_markdown("Zona _Villa Morra_, Asunción") == "Zona Villa Morra, Asunción"

    # --- Double underscore bold (__bold__) ---

    def test_double_underscore_bold(self):
        assert strip_markdown("__negrita__") == "negrita"

    # --- WhatsApp strikethrough (~text~) ---

    def test_wa_strikethrough(self):
        assert strip_markdown("~precio anterior~") == "precio anterior"

    def test_wa_strikethrough_mid_sentence(self):
        assert strip_markdown("Antes: ~USD 200.000~ ahora USD 180.000") == "Antes: USD 200.000 ahora USD 180.000"

    # --- Inline code (`code`) ---

    def test_inline_code(self):
        assert strip_markdown("`codigo`") == "codigo"

    def test_inline_code_mid_sentence(self):
        assert strip_markdown("Referencia: `ID-4521`") == "Referencia: ID-4521"

    # --- Headers ---

    def test_h1_header(self):
        assert strip_markdown("# Casa disponible") == "Casa disponible"

    def test_h3_header(self):
        assert strip_markdown("### Características") == "Características"

    # --- Bullets ---

    def test_bullet_dash(self):
        assert strip_markdown("- 3 dormitorios") == "3 dormitorios"

    def test_bullet_asterisk(self):
        assert strip_markdown("* Piscina incluida") == "Piscina incluida"

    # --- Links ---

    def test_markdown_link(self):
        assert strip_markdown("[Ver propiedad](https://example.com/prop/123)") == "Ver propiedad"

    # --- Mixed formatting (realistic bot message) ---

    def test_realistic_bot_message(self):
        msg = "*Casa en Lambaré* — USD 150.000\n_3 dormitorios_, 2 baños"
        result = strip_markdown(msg)
        assert "**" not in result
        assert "*" not in result
        assert "_" not in result
        assert "Casa en Lambaré" in result
        assert "3 dormitorios" in result

    def test_multiple_bold_in_one_message(self):
        msg = "**Tipo:** Departamento | **Precio:** USD 80.000"
        result = strip_markdown(msg)
        assert "**" not in result
        assert "Tipo:" in result
        assert "Precio:" in result
        assert "USD 80.000" in result

    # --- Truncated / unclosed markers should not corrupt output ---

    def test_unclosed_bold_marker_left_intact(self):
        # Single unmatched ** should not raise or corrupt surrounding text
        result = strip_markdown("Hola **negrita sin cerrar")
        assert "Hola" in result
        # The unmatched ** may remain but must not cause an error
        assert isinstance(result, str)

    def test_unclosed_italic_marker_left_intact(self):
        result = strip_markdown("precio _sin cerrar")
        assert "precio" in result
        assert isinstance(result, str)

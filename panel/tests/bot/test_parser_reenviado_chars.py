"""Tests for lead_parser.py — reenviado characteristics extraction.

Covers the new regex-based extraction of listing type, operation,
bedrooms, area, price and currency from reenviado lead messages.
"""
from __future__ import annotations

import pytest

from app.bot.services.infocasas.lead_parser import ParsedLead, parse_lead


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reenviado_lead(message: str) -> dict:
    """Build a minimal GraphQL leadById dict with a reenviado message."""
    return {
        "id": "99990001",
        "message": message,
        "created_at": "2026-04-10 09:00:00",
        "from": {
            "name": "Daniel Perez",
            "email": "daniel@example.com",
            "phone": "+595981500746",
            "whatsapp_phone": None,
            "has_whatsapp": False,
        },
        "listing": {
            "id": "777000",
            "title": "Casa en Luque",
            "code": "LQ99",
            "neighborhood": {"name": "Luque"},
        },
    }


def _direct_lead(message: str) -> dict:
    """Build a minimal GraphQL leadById dict with a direct (non-reenviado) message."""
    return {
        "id": "99990002",
        "message": message,
        "created_at": "2026-04-10 09:00:00",
        "from": {
            "name": "Maria Lopez",
            "email": "maria@example.com",
            "phone": "+595991234567",
            "whatsapp_phone": None,
            "has_whatsapp": False,
        },
        "listing": {
            "id": "888000",
            "title": "Departamento en Asuncion",
            "code": "AS01",
            "neighborhood": {"name": "Asuncion"},
        },
    }


FULL_MSG_GS = (
    "Hola, tenemos una consulta reenviada para vos.\n"
    "La propiedad consultada tenía las siguientes características:\n"
    "Apartamento en Alquiler de 3 dorms. en Recoleta, 125 m² por Gs. 6.500.000"
)

FULL_MSG_USD = (
    "Hola, tenemos una consulta reenviada para vos.\n"
    "La propiedad consultada tenía las siguientes características:\n"
    "Casa en Venta de 3 dorms. en Luque, 150 m² por USD 90.000"
)

_CASA_VENTA_MSG = (
    "Hola, tenemos una consulta reenviada para vos.\n"
    "La propiedad consultada tenía las siguientes características:\n"
    "Casa en Venta de 3 dorms. en Luque, 150 m² por USD 90.000\n"
    "Por favor contactá al cliente a la brevedad."
)

_DEPTO_ALQUILER_MSG = (
    "Consulta reenviada desde otra inmobiliaria.\n"
    "La propiedad consultada tenía las siguientes características:\n"
    "Departamento en Alquiler de 2 dorms. en Asunción, 80 m² por Gs 4.500.000\n"
    "Gracias."
)

_NO_CHARS_MSG = (
    "Esta es una consulta reenviada pero no tiene bloque de características.\n"
    "Contacto: cliente@email.com"
)


# ---------------------------------------------------------------------------
# Test 1: Extrae tipo casa en venta
# ---------------------------------------------------------------------------

class TestParseCasaVenta:
    """Reenviado message with Casa en Venta block."""

    def _parsed(self) -> ParsedLead:
        lead = parse_lead(_reenviado_lead(_CASA_VENTA_MSG))
        assert lead is not None
        return lead

    def test_listing_type_is_casa(self):
        assert self._parsed().listing_type == "casa"

    def test_listing_operation_is_venta(self):
        assert self._parsed().listing_operation == "venta"

    def test_listing_bedrooms_is_3(self):
        assert self._parsed().listing_bedrooms == 3

    def test_listing_city_from_neighborhood(self):
        # listing_city comes from neighborhood.name in the GraphQL data
        assert self._parsed().listing_city == "Luque"

    def test_listing_price_is_90000(self):
        assert self._parsed().listing_price == 90000.0

    def test_listing_currency_is_usd(self):
        assert self._parsed().listing_currency == "usd"


# ---------------------------------------------------------------------------
# Test 2: Extrae departamento en alquiler
# ---------------------------------------------------------------------------

class TestParseDepartamentoAlquiler:
    """Reenviado message with Departamento en Alquiler block."""

    def _parsed(self) -> ParsedLead:
        lead = parse_lead(_reenviado_lead(_DEPTO_ALQUILER_MSG))
        assert lead is not None
        return lead

    def test_listing_type_is_departamento(self):
        assert self._parsed().listing_type == "departamento"

    def test_listing_operation_is_alquiler(self):
        assert self._parsed().listing_operation == "alquiler"

    def test_listing_bedrooms_is_2(self):
        assert self._parsed().listing_bedrooms == 2

    def test_listing_price_is_4500000(self):
        assert self._parsed().listing_price == 4500000.0

    def test_listing_currency_is_gs(self):
        assert self._parsed().listing_currency == "gs"


# ---------------------------------------------------------------------------
# Test 3: Reenviado sin bloque de características → fallback graceful
# ---------------------------------------------------------------------------

class TestParseSinBloque:
    """Reenviado message without a characteristics block."""

    def _parsed(self) -> ParsedLead:
        lead = parse_lead(_reenviado_lead(_NO_CHARS_MSG))
        assert lead is not None
        return lead

    def test_listing_type_is_none(self):
        assert self._parsed().listing_type is None

    def test_listing_bedrooms_is_none(self):
        assert self._parsed().listing_bedrooms is None

    def test_listing_price_is_none(self):
        assert self._parsed().listing_price is None

    def test_is_reassigned_still_true(self):
        assert self._parsed().is_reassigned is True


# ---------------------------------------------------------------------------
# Test 4: Campo is_reassigned=True cuando mensaje tiene "consulta reenviada"
# ---------------------------------------------------------------------------

class TestIsReassignedTrue:
    """is_reassigned field is set correctly."""

    def test_is_reassigned_true_for_reenviado(self):
        lead = parse_lead(_reenviado_lead(_CASA_VENTA_MSG))
        assert lead is not None
        assert lead.is_reassigned is True

    def test_is_reassigned_case_insensitive(self):
        msg = "Consulta Reenviada para ti. La propiedad consultada tenía las siguientes características:\nCasa en Venta de 1 dorms. en Asunción, 100 m² por USD 50.000"
        lead = parse_lead(_reenviado_lead(msg))
        assert lead is not None
        assert lead.is_reassigned is True


# ---------------------------------------------------------------------------
# Test 5: Lead directo no toca los campos de parser reenviado
# ---------------------------------------------------------------------------

class TestDirectLeadNoReenviado:
    """Direct (non-reenviado) leads should have listing fields as None."""

    def test_listing_type_none_for_direct(self):
        msg = "Hola, me interesa la propiedad. ¿Podemos coordinar una visita?"
        lead = parse_lead(_direct_lead(msg))
        assert lead is not None
        assert lead.listing_type is None

    def test_listing_bedrooms_none_for_direct(self):
        msg = "Hola, me interesa la propiedad."
        lead = parse_lead(_direct_lead(msg))
        assert lead is not None
        assert lead.listing_bedrooms is None

    def test_is_reassigned_false_for_direct(self):
        msg = "Hola, me interesa la propiedad."
        lead = parse_lead(_direct_lead(msg))
        assert lead is not None
        assert lead.is_reassigned is False

    def test_listing_price_none_for_direct(self):
        msg = "Hola, me interesa la propiedad."
        lead = parse_lead(_direct_lead(msg))
        assert lead is not None
        assert lead.listing_price is None


# ---------------------------------------------------------------------------
# Test 6: Precio Gs. con punto (Bug 1A)
# ---------------------------------------------------------------------------

class TestPrecioGsConPunto:
    """'Gs. 6.500.000' (with period after Gs) must parse correctly."""

    def test_precio_gs_con_punto(self):
        lead = parse_lead(_reenviado_lead(FULL_MSG_GS))
        assert lead is not None
        assert lead.listing_price == 6500000.0
        assert lead.listing_currency == "gs"


# ---------------------------------------------------------------------------
# Test 7: Precio Gs sin punto
# ---------------------------------------------------------------------------

class TestPrecioGsSinPunto:
    """'Gs 6.500.000' (no period) must also parse correctly."""

    def test_precio_gs_sin_punto(self):
        msg_no_dot = FULL_MSG_GS.replace("Gs.", "Gs")
        lead = parse_lead(_reenviado_lead(msg_no_dot))
        assert lead is not None
        assert lead.listing_price == 6500000.0
        assert lead.listing_currency == "gs"


# ---------------------------------------------------------------------------
# Test 8: Precio USD
# ---------------------------------------------------------------------------

class TestPrecioUSD:
    """'USD 90.000' must parse correctly."""

    def test_precio_usd(self):
        lead = parse_lead(_reenviado_lead(FULL_MSG_USD))
        assert lead is not None
        assert lead.listing_price == 90000.0
        assert lead.listing_currency == "usd"


# ---------------------------------------------------------------------------
# Test 9: Zona simple (Bug 1B + 1C)
# ---------------------------------------------------------------------------

class TestZonaSimple:
    """'en Recoleta, 125 m²' → listing_zone_from_message = 'Recoleta'."""

    def test_zona_simple(self):
        lead = parse_lead(_reenviado_lead(FULL_MSG_GS))
        assert lead is not None
        assert lead.listing_zone_from_message == "Recoleta"


# ---------------------------------------------------------------------------
# Test 10: Zona con guion
# ---------------------------------------------------------------------------

class TestZonaConGuion:
    """'en Asunción - Recoleta, 200 m²' → zone includes the dash part."""

    def test_zona_con_guion(self):
        msg = (
            "Hola, tenemos una consulta reenviada para vos.\n"
            "La propiedad consultada tenía las siguientes características:\n"
            "Departamento en Alquiler de 2 dorms. en Asunción - Recoleta, 200 m² por USD 50.000"
        )
        lead = parse_lead(_reenviado_lead(msg))
        assert lead is not None
        assert lead.listing_zone_from_message == "Asunción - Recoleta"


# ---------------------------------------------------------------------------
# Test 11: Zona con coma (Bug 1C)
# ---------------------------------------------------------------------------

class TestZonaConComa:
    """'en Villa Morra, Asunción, 150 m²' → zone = 'Villa Morra, Asunción'."""

    def test_zona_con_coma(self):
        msg = (
            "Hola, tenemos una consulta reenviada para vos.\n"
            "La propiedad consultada tenía las siguientes características:\n"
            "Casa en Venta de 4 dorms. en Villa Morra, Asunción, 150 m² por USD 90.000"
        )
        lead = parse_lead(_reenviado_lead(msg))
        assert lead is not None
        assert lead.listing_zone_from_message == "Villa Morra, Asunción"


# ---------------------------------------------------------------------------
# Test 12: listing_zone_from_message se guarda en ParsedLead
# ---------------------------------------------------------------------------

class TestListingZoneFromMessageEnParsedLead:
    """Full parse must return a ParsedLead with listing_zone_from_message set."""

    def test_listing_zone_from_message_se_guarda_en_parsed_lead(self):
        lead = parse_lead(_reenviado_lead(FULL_MSG_GS))
        assert lead is not None
        assert hasattr(lead, "listing_zone_from_message")
        assert lead.listing_zone_from_message == "Recoleta"


# ---------------------------------------------------------------------------
# Test 13: listing_zone_from_message es None si no hay bloque
# ---------------------------------------------------------------------------

class TestZonaNoneSiNoHayBloque:
    """No characteristics block → listing_zone_from_message must be None."""

    def test_zona_none_si_no_hay_bloque(self):
        lead = parse_lead(_reenviado_lead(_NO_CHARS_MSG))
        assert lead is not None
        assert lead.listing_zone_from_message is None

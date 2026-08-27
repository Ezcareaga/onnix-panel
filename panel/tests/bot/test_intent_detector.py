"""Tests para handlers.intent_detector (M4 Task 3.3)."""
from __future__ import annotations

from app.bot.handlers.intent_detector import (
    detect_intent_from_text,
    is_pagination_text,
)


class TestIsPaginationText:
    def test_ver_mas_matches(self):
        assert is_pagination_text("ver más") is True
        assert is_pagination_text("Quiero ver mas opciones") is True

    def test_mostrame_mas_matches(self):
        assert is_pagination_text("mostrame más") is True
        assert is_pagination_text("muéstrame más propiedades") is True

    def test_las_demas_matches(self):
        assert is_pagination_text("las demás") is True
        assert is_pagination_text("los demas") is True

    def test_siguiente_matches(self):
        assert is_pagination_text("próximo") is True
        assert is_pagination_text("siguiente") is True

    def test_non_pagination_text_no_match(self):
        assert is_pagination_text("hola") is False
        assert is_pagination_text("quiero comprar una casa") is False
        assert is_pagination_text("") is False


class TestDetectIntentFromText:
    def test_saludo(self):
        assert detect_intent_from_text("Hola, ¿en qué te ayudo?") == "saludo"
        assert detect_intent_from_text("Bienvenido a Onnix") == "saludo"
        assert detect_intent_from_text("Buenos días") == "saludo"

    def test_lead(self):
        assert detect_intent_from_text("Te contactamos con un asesor") == "lead"
        assert detect_intent_from_text("Quiero contactar un asesor") == "lead"
        assert detect_intent_from_text("Te registro en el sistema") == "lead"

    def test_busqueda_incompleta_operacion(self):
        assert detect_intent_from_text("¿Querés comprar o alquilar?") == "busqueda_incompleta_operacion"
        assert detect_intent_from_text("Qué operación te interesa") == "busqueda_incompleta_operacion"

    def test_busqueda_incompleta_zona(self):
        assert detect_intent_from_text("¿En qué zona querés buscar?") == "busqueda_incompleta_zona"
        assert detect_intent_from_text("¿En qué barrio?") == "busqueda_incompleta_zona"
        # NOTA: el patrón es "donde busc" (sin acento) — comportamiento original preservado.
        assert detect_intent_from_text("donde buscas") == "busqueda_incompleta_zona"

    def test_conversacion_fallback(self):
        assert detect_intent_from_text("Ok, entendido") == "conversacion"
        assert detect_intent_from_text("gracias") == "conversacion"

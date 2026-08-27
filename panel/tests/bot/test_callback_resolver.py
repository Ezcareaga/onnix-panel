"""Tests para handlers.callback_resolver.translate_callback (M4 Task 3.2)."""
from __future__ import annotations

from app.bot.core.types import ConversationState
from app.bot.handlers.callback_resolver import (
    _CALLBACK_TRANSLATIONS,
    translate_callback,
)


def test_translate_detail_n_resolves_from_current_page_ids():
    ctx = ConversationState(current_page_ids=[100, 200, 300])
    assert translate_callback("detail_1", ctx) == "Dame detalle de la propiedad 100"
    assert translate_callback("detail_2", ctx) == "Dame detalle de la propiedad 200"
    assert translate_callback("detail_3", ctx) == "Dame detalle de la propiedad 300"


def test_translate_detail_out_of_range_returns_none():
    ctx = ConversationState(current_page_ids=[100, 200])
    assert translate_callback("detail_5", ctx) is None


def test_translate_detail_empty_page_ids_returns_none():
    ctx = ConversationState(current_page_ids=[])
    assert translate_callback("detail_1", ctx) is None


def test_translate_detail_malformed_returns_none():
    ctx = ConversationState(current_page_ids=[100])
    assert translate_callback("detail_abc", ctx) is None
    assert translate_callback("detail_", ctx) is None


def test_translate_btn_detalle_legacy_resolves_from_id():
    ctx = ConversationState()
    assert translate_callback("BTN_DETALLE_42", ctx) == "Dame detalle de la propiedad 42"
    assert translate_callback("BTN_DETALLE_755934", ctx) == "Dame detalle de la propiedad 755934"


def test_translate_static_hablar_asesor():
    ctx = ConversationState()
    assert translate_callback("hablar_asesor", ctx) == "Quiero hablar con un asesor humano"


def test_translate_static_search_compra():
    ctx = ConversationState()
    assert translate_callback("SEARCH_COMPRA", ctx) == "Quiero comprar una propiedad"


def test_translate_static_si_mostrame_reenviado():
    ctx = ConversationState()
    assert translate_callback("SI_MOSTRAME_REENVIADO", ctx) == "Sí, mostrame propiedades disponibles"


def test_translate_unknown_callback_returns_none():
    ctx = ConversationState()
    assert translate_callback("UNKNOWN_FOO", ctx) is None
    assert translate_callback("random", ctx) is None


def test_translations_dict_only_has_active_callbacks():
    """Drift guard: el dict tiene los 5 canónicos + 6 alias de templates M3.

    Los alias M3 (view_details, talk_to_agent, intent_comprar,
    intent_alquilar, followup_view, followup_72h_view) traducen los IDs
    legacy que los templates aprobados por Meta siguen emitiendo, sin
    requerir un re-submit a Meta para canonicalizar el naming.
    """
    assert set(_CALLBACK_TRANSLATIONS.keys()) == {
        # canonical
        "hablar_asesor",
        "SEARCH_COMPRA",
        "SEARCH_ALQUILER",
        "SI_MOSTRAME_REENVIADO",
        "AHORA_NO_REENVIADO",
        # M3 template aliases (Fase 5 Opción A)
        "view_details",
        "talk_to_agent",
        "intent_comprar",
        "intent_alquilar",
        "followup_view",
        "followup_72h_view",
    }

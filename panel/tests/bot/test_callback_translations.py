"""Drift guards for _CALLBACK_TRANSLATIONS in orchestrator.

Ensures the ~28 legacy N8N-era callbacks stay deleted (audit confirmed 0
uses in 60 days of production data). If any reappears in the dict, these
tests catch it at CI time.

See docs/AUDIT_M4_FASE0_20260419.md §3.2 for the full rationale and
PLAN_M4_REFACTOR.md Task 1.1 for the deletion commit.
"""
from __future__ import annotations

from app.bot.handlers.callback_resolver import _CALLBACK_TRANSLATIONS


def test_no_btn_wizard_legacy_callbacks():
    """BTN_* wizard callbacks from N8N era están todos eliminados."""
    legacy = {
        "BTN_BUSCAR", "BTN_ASESOR", "BTN_SEGUIR_BUSCANDO", "BTN_NUEVA_BUSQUEDA",
        "BTN_CAMBIAR_ZONA", "BTN_CAMBIAR_PRESUPUESTO", "BTN_SIN_FILTROS",
        "BTN_VISITA", "BTN_CONFIRMAR_VISITA", "BTN_OTRO_HORARIO",
        "BTN_COMPRAR", "BTN_ALQUILAR", "BTN_VENDER",
        "BTN_MAS", "BTN_MAS_PROPS",  # cleaned in Task 1.2
    }
    reappeared = legacy & set(_CALLBACK_TRANSLATIONS.keys())
    assert reappeared == set(), f"Callbacks BTN_* legacy reaparecieron: {reappeared}"


def test_no_search_wizard_legacy_callbacks():
    """SEARCH_* wizard callbacks deleted (except SEARCH_COMPRA/SEARCH_ALQUILER, aún enviados)."""
    legacy = {
        "SEARCH_CASA", "SEARCH_DEPTO", "SEARCH_TERRENO", "SEARCH_DEPTO_POZO",
        "SEARCH_DUPLEX", "SEARCH_OFICINA", "SEARCH_SI_MOSTRAME", "SEARCH_NO_GRACIAS",
    }
    reappeared = legacy & set(_CALLBACK_TRANSLATIONS.keys())
    assert reappeared == set(), f"Callbacks SEARCH_ legacy reaparecieron: {reappeared}"


def test_no_case_variant_legacy_callbacks():
    """Variantes de case y lowercase duplicadas deleted."""
    legacy = {
        "VER_SIMILARES", "si_mostrame", "no_interesado",
        "comprar", "alquilar", "vender",
        "hablar_con_asesor", "ver_similares",
    }
    reappeared = legacy & set(_CALLBACK_TRANSLATIONS.keys())
    assert reappeared == set(), f"Callbacks case-variant legacy reaparecieron: {reappeared}"


def test_no_followup_legacy_callbacks():
    """Callbacks de followup legacy deleted."""
    legacy = {"followup_si", "followup_no", "FOLLOWUP_SI", "FOLLOWUP_NO"}
    reappeared = legacy & set(_CALLBACK_TRANSLATIONS.keys())
    assert reappeared == set(), f"Callbacks followup legacy reaparecieron: {reappeared}"


def test_no_agendar_visita_callback():
    """agendar_visita no se envía por ningún template — deleted."""
    assert "agendar_visita" not in _CALLBACK_TRANSLATIONS


def test_kept_active_callbacks_present():
    """Los callbacks activos NO deben haberse borrado por accidente."""
    required = {
        "hablar_asesor",
        "SEARCH_COMPRA",
        "SEARCH_ALQUILER",
        "SI_MOSTRAME_REENVIADO",
        "AHORA_NO_REENVIADO",
    }
    missing = required - set(_CALLBACK_TRANSLATIONS.keys())
    assert missing == set(), f"Callbacks activos borrados por error: {missing}"


def test_dict_size_matches_active_plus_m3_aliases():
    """Dict tiene 11 entries: 5 callbacks canónicos + 6 alias de templates M3.

    M3 aliases (post M4 Fase 5 Opción A): view_details, talk_to_agent,
    intent_comprar, intent_alquilar, followup_view, followup_72h_view.
    Ver docstring de _CALLBACK_TRANSLATIONS para la justificación.
    """
    assert len(_CALLBACK_TRANSLATIONS) == 11, (
        f"Dict tiene {len(_CALLBACK_TRANSLATIONS)} entries — esperado exactamente 11 "
        f"(5 canónicos + 6 alias M3)"
    )


def test_m3_template_aliases_resolve_to_canonical_phrases():
    """Los 6 IDs de los templates M3 v3/v4 traducen al mismo lenguaje natural
    que sus pares canónicos, así el orchestrator los maneja igual sin
    warning + roundtrip extra.
    """
    aliases = {
        "view_details":      "Quiero ver el detalle de esta propiedad",
        "talk_to_agent":     "Quiero hablar con un asesor humano",
        "intent_comprar":    "Quiero comprar una propiedad",
        "intent_alquilar":   "Quiero alquilar una propiedad",
        "followup_view":     "Quiero ver el detalle de esta propiedad",
        "followup_72h_view": "Quiero ver el detalle de esta propiedad",
    }
    for key, expected in aliases.items():
        assert _CALLBACK_TRANSLATIONS.get(key) == expected, (
            f"M3 alias {key!r} debería resolver a {expected!r}, "
            f"obtuvo {_CALLBACK_TRANSLATIONS.get(key)!r}"
        )

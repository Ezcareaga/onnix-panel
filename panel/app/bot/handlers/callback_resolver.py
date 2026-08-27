"""Callback resolver — translate button/list payloads to natural language.

When a user taps a WhatsApp or Telegram button, Twilio/TG sends a callback
payload (e.g. ``detail_1``, ``SEARCH_COMPRA``). The orchestrator needs to
convert it into a Spanish phrase Claude can interpret as user intent.

Extracted from orchestrator.py in M4 Task 3.2 — was previously a static
method on the Orchestrator class. See PLAN_M4_REFACTOR.md §Fase 3.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.bot.core.types import ConversationState


# Currently active callbacks sent by response_builder.py / templates.
# Legacy N8N-era callbacks (~34) were removed in M4 Task 1.1 after
# confirming 0 uses in 60 days of production data. See
# docs/AUDIT_M4_FASE0_20260419.md §3.2 and the drift guard at
# panel/tests/bot/test_callback_translations.py
#
# The 6 entries marked "M3 template alias" are the IDs that the M3 v3/v4
# Meta-approved templates emit when a user taps a quick-reply button. M3
# kept the legacy naming for those `id` fields (decision: bodies were
# la administradora-validated and a re-submit only to rename callback IDs would
# require another 24-48h Meta cycle for no user-facing benefit). We map
# them here to the same natural-language phrases the canonical IDs already
# resolve to, so the orchestrator handles them uniformly without a warning
# log + extra Claude roundtrip. Drop these aliases if/when those templates
# are re-issued with canonical IDs.
_CALLBACK_TRANSLATIONS: dict[str, str] = {
    "hablar_asesor": "Quiero hablar con un asesor humano",
    # response_builder.py:493-494 still sends these for busqueda_incompleta_operacion
    "SEARCH_COMPRA": "Quiero comprar una propiedad",
    "SEARCH_ALQUILER": "Quiero alquilar una propiedad",
    # Reenviado welcome template (onnix_ic_reenviado_welcome_v3)
    "SI_MOSTRAME_REENVIADO": "Sí, mostrame propiedades disponibles",
    "AHORA_NO_REENVIADO": "Ahora no me interesa, gracias",
    # M3 template aliases — see module docstring above.
    "view_details": "Quiero ver el detalle de esta propiedad",
    "talk_to_agent": "Quiero hablar con un asesor humano",
    "intent_comprar": "Quiero comprar una propiedad",
    "intent_alquilar": "Quiero alquilar una propiedad",
    "followup_view": "Quiero ver el detalle de esta propiedad",
    "followup_72h_view": "Quiero ver el detalle de esta propiedad",
}


def translate_callback(
    callback_data: str,
    search_context: "ConversationState",
) -> str | None:
    """Translate a button callback to natural language for Claude.

    Resolves ``detail_N`` via ``search_context.current_page_ids`` and
    ``BTN_DETALLE_{id}`` (legacy format) dynamically. Returns ``None``
    for unknown callbacks — caller falls back to ButtonText from Twilio.
    """
    # detail_N → resolve property ID from current page
    if callback_data.startswith("detail_"):
        try:
            idx = int(callback_data.split("_")[1]) - 1
        except (IndexError, ValueError):
            return None
        page_ids = search_context.current_page_ids
        if 0 <= idx < len(page_ids):
            return f"Dame detalle de la propiedad {page_ids[idx]}"
        return None

    # BTN_DETALLE_{id} (legacy N8N format)
    if callback_data.startswith("BTN_DETALLE_"):
        prop_id = callback_data.replace("BTN_DETALLE_", "")
        return f"Dame detalle de la propiedad {prop_id}"

    # Static translations
    return _CALLBACK_TRANSLATIONS.get(callback_data)

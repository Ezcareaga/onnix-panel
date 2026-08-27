"""Pydantic schemas for WhatsApp template sending."""
from pydantic import BaseModel, field_validator

ALLOWED_TEMPLATE_KEYS = {
    # ── Legacy v1 templates — mantenidos por backward compat para agentes
    # que tengan workflow manual con el naming viejo. Se borrarán cuando
    # Ez/la administradora confirmen que no los usan. wa_tpl_followup ya fue
    # removido (bot interno migró a wa_tpl_followup_v3, ningún agente
    # lo enviaba manualmente desde el panel).
    "wa_tpl_send_generic",
    "wa_tpl_send_preferences",
    "wa_tpl_send_property",
    # ── M3 templates aprobados (Onnix identity, v2/v3/v4) — activos en prod ──
    "wa_tpl_agent_reply_v3",
    "wa_tpl_followup_72h_v3",
    "wa_tpl_followup_v3",
    "wa_tpl_ic_recurrente_directo_v2",
    "wa_tpl_ic_recurrente_reenviado_v2",
    "wa_tpl_ic_reenviado_welcome_v3",
    "wa_tpl_ic_welcome_v3",
    "wa_tpl_send_generic_v3",
    "wa_tpl_send_preferences_v4",
    "wa_tpl_send_property_v4",
}


class SendTemplateRequest(BaseModel):
    contact_id: int
    template_key: str

    @field_validator("template_key")
    @classmethod
    def validate_template_key(cls, v: str) -> str:
        if v not in ALLOWED_TEMPLATE_KEYS:
            raise ValueError(
                f"template_key must be one of: {', '.join(sorted(ALLOWED_TEMPLATE_KEYS))}"
            )
        return v

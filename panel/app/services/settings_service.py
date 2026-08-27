import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.bot_setting_repo import bot_setting_repo

logger = logging.getLogger(__name__)

# SEC-01: claves de bot_settings que contienen credenciales máquina-a-máquina
# (infocasas_phpsessid = sesión PHP válida, infocasas_frontend_token = JWT).
# Se gestionan por DB/servicios (session_manager, infocasas_service) y NUNCA
# deben viajar al template de /settings — ni la key ni el value.
# Nota: wa_tpl_* (Content SIDs de Twilio, HX...) intencionalmente NO matchean:
# son identificadores de template, no secretos.
_SENSITIVE_KEY_RE = re.compile(
    r"(token|secret|sessid|jwt|password|api_key|phpsessid)", re.I
)

# Allowed values for the global bot_default_mode setting (BOT-01).
# bot_settings is a key/value table — this set is the application-layer CHECK
# (mirrors toggle_whatsapp_mode, which constrains {'auto','manual'} in code).
BOT_DEFAULT_MODES = ("recepcionista", "busqueda")

ALLOWED_SETTINGS = frozenset({
    "bot_off_message",
    "vip_price_threshold_usd",
    "infocasas_poll_interval_min",
    "infocasas_wa_delay_min",
    "infocasas_wa_delay_max",
    "infocasas_reply_delay_min",
    "infocasas_reply_delay_max",
    "working_hours_start",
    "working_hours_end",
    "human_cooldown_minutes",
})


class SettingsService:
    @staticmethod
    async def get_all_settings(db: AsyncSession) -> dict:
        # SEC-01: filtrar credenciales — el value de keys sensibles nunca
        # sale del service (no llega ni al route ni al template).
        settings = [
            s
            for s in await bot_setting_repo.get_all(db)
            if not _SENSITIVE_KEY_RE.search(s.key)
        ]
        bot_enabled_raw = await bot_setting_repo.get_value(db, "bot_enabled")
        whatsapp_mode = await bot_setting_repo.get_value(db, "whatsapp_mode")
        ic_autoreply_raw = await bot_setting_repo.get_value(db, "ic_autoreply_enabled")
        followup_enabled_raw = await bot_setting_repo.get_value(db, "scheduler_followup_sender_enabled")
        ic_reenviados_enabled_raw = await bot_setting_repo.get_value(db, "ic_autoreply_reenviados_enabled")
        bot_default_mode_raw = await bot_setting_repo.get_value(db, "bot_default_mode")
        return {
            "settings": settings,
            "bot_enabled": bot_enabled_raw == "true" if bot_enabled_raw else False,
            "whatsapp_mode": whatsapp_mode or "manual",
            "ic_autoreply_enabled": ic_autoreply_raw != "false",
            "followup_enabled": followup_enabled_raw == "true",
            "ic_reenviados_enabled": ic_reenviados_enabled_raw == "true",
            # Defensive default to 'busqueda' for absent/unexpected values (BOT-01).
            "bot_default_mode": bot_default_mode_raw
            if bot_default_mode_raw in BOT_DEFAULT_MODES
            else "busqueda",
        }

    @staticmethod
    async def toggle_bot(db: AsyncSession, user_id: int) -> bool:
        current = await bot_setting_repo.get_value(db, "bot_enabled")
        new_value = "false" if current == "true" else "true"
        await bot_setting_repo.update_value(db, "bot_enabled", new_value, user_id)
        logger.info("Bot toggled: new_value=%s, by user_id=%s", new_value, user_id)
        return new_value == "true"

    @staticmethod
    async def toggle_followup_sender(db: AsyncSession, user_id: int) -> bool:
        """Toggle scheduler_followup_sender_enabled. Returns new bool state."""
        current = await bot_setting_repo.get_value(db, "scheduler_followup_sender_enabled")
        new_value = "false" if current == "true" else "true"
        await bot_setting_repo.update_value(db, "scheduler_followup_sender_enabled", new_value, user_id)
        logger.info("Followup sender toggled: new_value=%s, by user_id=%s", new_value, user_id)
        return new_value == "true"

    @staticmethod
    async def toggle_ic_reenviados(db: AsyncSession, user_id: int) -> bool:
        """Toggle ic_autoreply_reenviados_enabled. Returns new bool state."""
        current = await bot_setting_repo.get_value(db, "ic_autoreply_reenviados_enabled")
        new_value = "false" if current == "true" else "true"
        await bot_setting_repo.update_value(db, "ic_autoreply_reenviados_enabled", new_value, user_id)
        logger.info("IC reenviados toggled: new_value=%s, by user_id=%s", new_value, user_id)
        return new_value == "true"

    @staticmethod
    async def toggle_whatsapp_mode(db: AsyncSession, user_id: int) -> str:
        current = await bot_setting_repo.get_value(db, "whatsapp_mode")
        new_value = "auto" if current == "manual" else "manual"
        await bot_setting_repo.update_value(db, "whatsapp_mode", new_value, user_id)
        logger.info("WhatsApp mode toggled: new_value=%s, by user_id=%s", new_value, user_id)
        return new_value

    @staticmethod
    async def toggle_ic_autoreply(db: AsyncSession, user_id: int) -> bool:
        current = await bot_setting_repo.get_value(db, "ic_autoreply_enabled")
        new_value = "false" if current != "false" else "true"
        await bot_setting_repo.update_value(db, "ic_autoreply_enabled", new_value, user_id)
        logger.info("IC autoreply toggled: new_value=%s, by user_id=%s", new_value, user_id)
        return new_value == "true"

    @staticmethod
    async def set_bot_default_mode(db: AsyncSession, mode: str, user_id: int) -> str:
        """Set the global bot mode (BOT-01). Dedicated toggle, NOT routed
        through ALLOWED_SETTINGS/update_setting — mirrors the M6.1
        dedicated-toggle pattern.

        The allowed set {recepcionista, busqueda} is the application-layer
        CHECK (bot_settings has no column CHECK). Uses upsert so it works
        whether or not migration 041 has seeded the row yet.
        """
        if mode not in BOT_DEFAULT_MODES:
            logger.warning("Rejected bot_default_mode: %r (not allowed)", mode)
            raise ValueError(f"Invalid bot_default_mode: {mode!r}")
        await bot_setting_repo.upsert(db, "bot_default_mode", mode, user_id=user_id)
        logger.info("bot_default_mode set: new_value=%s, by user_id=%s", mode, user_id)
        return mode

    @staticmethod
    async def update_setting(db: AsyncSession, key: str, value: str, user_id: int) -> None:
        if key not in ALLOWED_SETTINGS:
            logger.warning("Rejected setting update: key=%s (not allowed)", key)
            raise ValueError(f"Setting '{key}' is not editable via this endpoint")
        await bot_setting_repo.update_value(db, key, value, user_id)
        logger.info("Setting updated: key=%s, by user_id=%s", key, user_id)

settings_service = SettingsService()

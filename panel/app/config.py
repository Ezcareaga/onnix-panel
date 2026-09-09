from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

class Settings:
    SECRET_KEY: str = os.environ.get("PANEL_SECRET_KEY", "change-me-in-production")
    POSTGRES_USER: str = os.environ["POSTGRES_USER"]
    POSTGRES_PASSWORD: str = os.environ["POSTGRES_PASSWORD"]
    POSTGRES_DB: str = os.environ.get("POSTGRES_DB", "onnix_prod")
    POSTGRES_HOST: str = os.environ.get("POSTGRES_HOST", "onnix-postgres")
    POSTGRES_PORT: str = os.environ.get("POSTGRES_PORT", "5432")
    ENVIRONMENT: str = os.environ.get("ENVIRONMENT", "development")
    TRUST_PROXY_HEADERS: bool = os.environ.get("TRUST_PROXY_HEADERS", "false").lower() in ("1", "true", "yes")
    SESSION_INACTIVITY_MINUTES: int = int(os.environ.get("SESSION_INACTIVITY_MINUTES", "60"))

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def cookie_secure(self) -> bool:
        """Marca `Secure` en las cookies de sesion y de CSRF.

        Default true: produccion y staging van detras de nginx con TLS. Se
        apaga SOLO para servir por http —la laptop—, donde Safari y Firefox
        descartan una cookie `Secure` y el login muere con el 403 de CSRF.

        Sin rama escondida para pytest: la suite apaga la variable en
        `tests/conftest.py` como cualquier otro entorno http. Una rama que solo
        existe bajo pytest es justamente lo que dejo pasar ese 403 en la
        laptop — el unico entorno http que nadie testeaba.

        Lee el entorno en cada acceso (como `is_production`) y no en la
        definicion de la clase, para que se pueda testear sin reimportar.
        """
        return os.environ.get("COOKIE_SECURE", "true").lower() in ("1", "true", "yes")

    @property
    def is_production(self) -> bool:
        """True only when ENVIRONMENT=='production' AND not running under pytest.

        The pytest guard mirrors the pattern already used in config.py and
        main.py (PYTEST_CURRENT_TEST env var), ensuring the test suite never
        treats itself as a production environment.
        """
        return (
            self.ENVIRONMENT == "production"
            and os.environ.get("PYTEST_CURRENT_TEST") is None
        )

settings = Settings()

# SECURITY: Reject the insecure default SECRET_KEY.
# The assertion is skipped during pytest runs to avoid breaking the test suite
# when PANEL_SECRET_KEY is not set in the test environment.
import os as _os
if not _os.environ.get("PYTEST_CURRENT_TEST"):
    assert settings.SECRET_KEY != "change-me-in-production", \
        "FATAL: Set PANEL_SECRET_KEY in .env -- default value is insecure"


def validate_required_secrets(
    *,
    force_production: bool | None = None,
    twilio_auth_token: str | None = None,
) -> None:
    """Validate that the signing secrets are present.

    In production, missing secrets abort boot with a RuntimeError naming
    the missing secret.  In non-production environments, missing secrets
    only produce a warning (dev-mode skip is acceptable).

    El corte es de seguridad y no es cosmetico: sin el secreto de firma la app
    aceptaria webhooks sin verificar, y eso es un agujero. ``scheduler_lifespan``
    es el lifespan de TODA la app (``main.py``), asi que abortar aca tumba el
    panel — por eso solo abortan los secretos de firma, nunca una key opcional.

    ``TELEGRAM_WEBHOOK_SECRET`` se fue el 2026-09-09 con el canal, y
    ``GEMINI_API_KEY`` con el ultimo cliente de LLM.

    Parameters
    ----------
    force_production:
        Override the is_production check.  Used by tests to exercise the
        validator without depending on the global PYTEST guard.
        Defaults to ``settings.is_production`` when None.
    twilio_auth_token:
        The Twilio auth token to validate.  Defaults to the value from
        ``bot_settings`` when None.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    if force_production is None:
        force_production = settings.is_production

    if twilio_auth_token is None:
        from app.bot.config import bot_settings as _bs
        twilio_auth_token = _bs.TWILIO_AUTH_TOKEN

    missing = []
    if not twilio_auth_token:
        missing.append("TWILIO_AUTH_TOKEN")

    if not missing:
        return

    if force_production:
        raise RuntimeError(
            f"FATAL: Required secret(s) missing in production: "
            f"{', '.join(missing)}. "
            f"Set them in .env and restart."
        )
    else:
        _log.warning(
            "Secreto(s) no configurado(s) (dev mode): %s — "
            "la verificacion de firma se saltea.",
            ", ".join(missing),
        )

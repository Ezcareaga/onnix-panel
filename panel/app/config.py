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
    telegram_webhook_secret: str | None = None,
    gemini_api_key: str | None = None,
) -> None:
    """Validate that the secrets the bot needs to run are present.

    In production, missing secrets abort boot with a RuntimeError naming
    the missing secret.  In non-production environments, missing secrets
    only produce a warning (dev-mode skip is acceptable).

    Se llamaba ``validate_webhook_secrets``.  Cambio de nombre porque ahora
    tambien mira ``GEMINI_API_KEY``, que no es un secreto de firma — y por eso
    **no aborta el boot aunque falte**.

    La diferencia importa y no es cosmetica.  Sin los secretos de firma la app
    aceptaria webhooks sin verificar: eso es un agujero de seguridad y el boot
    tiene que morir.  Sin la key de Gemini el bot arranca degradado pero
    arranca, y el panel anda perfecto — la key esta vacia a proposito desde que
    se perdieron los embeddings.  Abortar el boot por eso tumbaria el panel
    entero, que es lo que se usa todos los dias.  ``scheduler_lifespan`` es el
    lifespan de TODA la app (``main.py:26``), no solo del bot.

    Asi que Gemini avisa y no mata — ni aca ni en ``get_bot_dependencies()``,
    que desde el 2026-08-24 arma el grafo sin ``GeminiClient`` en vez de
    levantar ``RuntimeError`` y llevarse puesto el mensaje entrante.

    Parameters
    ----------
    force_production:
        Override the is_production check.  Used by tests to exercise the
        validator without depending on the global PYTEST guard.
        Defaults to ``settings.is_production`` when None.
    twilio_auth_token:
        The Twilio auth token to validate.  Defaults to the value from
        ``bot_settings`` when None.
    telegram_webhook_secret:
        The Telegram webhook secret to validate.  Defaults to the value
        from ``bot_settings`` when None.
    gemini_api_key:
        La API key de Gemini.  Defaults to the value from ``bot_settings``
        when None.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    if force_production is None:
        force_production = settings.is_production

    if twilio_auth_token is None:
        from app.bot.config import bot_settings as _bs
        twilio_auth_token = _bs.TWILIO_AUTH_TOKEN

    if telegram_webhook_secret is None:
        from app.bot.config import bot_settings as _bs
        telegram_webhook_secret = _bs.TELEGRAM_WEBHOOK_SECRET

    if gemini_api_key is None:
        from app.bot.config import bot_settings as _bs
        gemini_api_key = _bs.GEMINI_API_KEY

    if not gemini_api_key:
        _log.warning(
            "GEMINI_API_KEY vacia — el bot arranca DEGRADADO: la busqueda "
            "queda SQL puro (sin pierna vectorial) y el fallback del circuit "
            "breaker queda en el texto fijo. El panel funciona igual. "
            "Ver TD-OPS-01."
        )

    missing = []
    if not twilio_auth_token:
        missing.append("TWILIO_AUTH_TOKEN")
    if not telegram_webhook_secret:
        missing.append("TELEGRAM_WEBHOOK_SECRET")

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
            "la verificacion de firma se saltea y el bot corre degradado.",
            ", ".join(missing),
        )

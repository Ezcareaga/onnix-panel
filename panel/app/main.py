"""Onnix SA -- Admin Panel"""
import logging
import mimetypes
import os
import uuid
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.config import settings
from app.bot.scheduler.lifespan import scheduler_lifespan

logger = logging.getLogger(__name__)

# Python's stdlib mimetypes module does not know about .webp on every system.
# Without this, Starlette's StaticFiles serves /images/*.webp as text/plain
# and browsers refuse to render them. Production nginx serves /images/ directly,
# so the bug only surfaces when requests fall through to FastAPI (e.g. staging).
mimetypes.add_type("image/webp", ".webp")

app = FastAPI(
    title="Onnix Panel",
    docs_url=None,
    redoc_url=None,
    lifespan=scheduler_lifespan,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="onnix_session",
    max_age=86400,
    same_site="lax",
    https_only=os.getenv("PYTEST_CURRENT_TEST") is None,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Las fotos se montan POR ALIAS, nunca el árbol entero.
#
# Montar `/images` sobre `/app/images` dejaba servible `/images/remax/...` — y
# no sólo en staging: en producción la request que nginx no matchea cae al
# `location /`, llega al proxy y la resuelve este mount. Sacar el `location`
# general de nginx no alcanzaba: la puerta de atrás estaba acá.
#
# El mapa vive en `app/utils/fotos.py`, que es el mismo que arma las URLs.
if os.path.isdir("/app/images"):
    from app.utils.fotos import ALIAS_POR_FUENTE

    for _fuente, _alias in ALIAS_POR_FUENTE.items():
        _dir = f"/app/images/{_fuente}"
        if os.path.isdir(_dir):
            app.mount(
                f"/images/{_alias}",
                StaticFiles(directory=_dir),
                name=f"images-{_alias}",
            )

# Los MP4 de los tutoriales NO se montan con StaticFiles.
#
# Estuvieron montados en `/videos` un rato, y el mount es exactamente el mismo
# error que el `location /images/` general que el comentario de arriba explica,
# en otra capa: un mount de StaticFiles queda FUERA de `get_current_user`, así
# que `/tutoriales` pedía sesión y el MP4 lo bajaba cualquiera con la URL. Con
# un tutorial restringido a admin —el de usuarios— eso deja de ser una molestia
# y pasa a ser el agujero.
#
# Los sirve `GET /videos/{nombre}` en `app/routes/tutoriales.py`, que pide
# sesión, chequea el rol y recién ahí mira el disco.

from app.routes.auth import router as auth_router
from app.routes.dashboard import router as dashboard_router
from app.routes.leads import router as leads_router
from app.routes.stats import router as stats_router
from app.routes.conversations import router as conversations_router
from app.routes.contacts import router as contacts_router
from app.routes.visits import router as visits_router
from app.routes.settings import router as settings_router
from app.routes.users import router as users_router
from app.routes.api import router as api_router
from app.routes.events import router as events_router
from app.routes.bot_health import router as bot_health_router
from app.routes.public import router as public_router
from app.routes.properties import router as properties_router
from app.routes.admin_audit import router as admin_audit_router
from app.routes.me import router as me_router
from app.routes.tutoriales import router as tutoriales_router
from app.bot.webhooks import webhook_router
from starlette.requests import Request
from starlette.responses import Response

from app.tz import get_templates
_templates = get_templates()

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(leads_router)
app.include_router(stats_router)
app.include_router(conversations_router)
app.include_router(contacts_router)
app.include_router(visits_router)
app.include_router(settings_router)
app.include_router(users_router)
app.include_router(public_router)
app.include_router(events_router)
app.include_router(bot_health_router)
app.include_router(properties_router)
app.include_router(admin_audit_router)
app.include_router(me_router)
app.include_router(tutoriales_router)
app.include_router(api_router, prefix="/api")
app.include_router(webhook_router)

_CSP_HEADER_VALUE = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
    "https://static.cloudflareinsights.com; "
    # Ni una familia de terceros. fonts.bunny.net se fue con la migracion del
    # shell a Outfit self-hosteada; googleapis/gstatic seguian abiertos solo
    # por el portal publico, que pedia Cormorant Garamond al CDN de Google en
    # las dos vistas. Cormorant salio del proyecto (tanda 2 de
    # docs/audit/PORTAL_PLAN_20260823.md) y con ella los dos origenes.
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; "
    "img-src 'self' data: https:; "
    "connect-src 'self'"
    "; frame-src 'self' https://www.google.com"
)


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Content-Security-Policy"] = _CSP_HEADER_VALUE
    # Rutas dual full/partial (dashboard, leads, conversations, stats,
    # properties, bot_health, admin_audit) sirven contenido distinto en la
    # MISMA URL según el header HX-Request. Sin Vary, el browser cachea el
    # partial para la URL y el botón Atrás renderiza el partial sin layout.
    existing_vary = response.headers.get("Vary")
    response.headers["Vary"] = (
        f"{existing_vary}, HX-Request" if existing_vary else "HX-Request"
    )
    if request.headers.get("HX-Request"):
        # Un fragmento HTMX jamás debe quedar cacheado como documento.
        response.headers["Cache-Control"] = "no-store"
    return response

class CsrfMiddleware:
    """CSRF defense via double-submit cookie pattern (D1) — pure ASGI implementation.

    Design (OWASP double-submit cookie):
      1. On every request, read the csrf_token cookie. If absent, generate one.
      2. Expose the token as request.state.csrf_token so Jinja2 templates can
         embed it in hidden form fields (mapped via scope["state"]["csrf_token"]).
      3. For unsafe methods (POST/PUT/PATCH/DELETE) on non-webhook paths:
           - Under normal pytest: skip enforcement (mirrors https_only precedent).
             Existing POST tests are not broken.
           - When CSRF_FORCE_ENFORCE=1 (test-injectable) OR in production
             (PYTEST_CURRENT_TEST absent): enforce double-submit validation.
           - On failure: return 403 HTML (Spanish body, matches original style).
      4. When enforcement is needed AND the header token is absent, buffer the
         full request body from the ASGI receive stream, parse the urlencoded
         csrf_token field, then REPLAY the buffered body to the downstream app
         via a wrapped receive callable. This is the key fix: the original
         @app.middleware("http") implementation consumed the body via
         request.form() inside BaseHTTPMiddleware, which does NOT replay the
         body downstream — causing FastAPI's Form(...) parameters to arrive
         empty and breaking all form POSTs under enforcement.
      5. If we issued a NEW token, inject a Set-Cookie header into the
         http.response.start ASGI message via MutableHeaders so the cookie
         reaches the client without a round-trip through StreamingResponse.

    Webhook paths (/webhook/) are exempt — they use Twilio HMAC auth.

    Why pure ASGI instead of BaseHTTPMiddleware:
      BaseHTTPMiddleware wraps the ASGI app in a way that does NOT replay a
      consumed receive stream. Once request.form() / request.body() is called
      inside the middleware, the ASGI receive() is exhausted. Downstream FastAPI
      route handlers that also call receive() (implicitly via Form(...)) receive
      empty bytes. The pure ASGI pattern lets us buffer the body ourselves and
      supply a new async receive callable that yields the buffered bytes, so the
      downstream handler sees the full body regardless.

    References used:
      - Starlette pure ASGI middleware (class-based __call__ pattern):
        https://www.starlette.io/middleware/#pure-asgi-middleware
      - Starlette MutableHeaders for injecting response headers:
        https://www.starlette.io/datastructures/#mutableheaders
      - request.state ↔ scope["state"] mapping (Starlette Request.state property):
        starlette/requests.py::HTTPConnection.state — setdefault("state", {}) on scope
      - OWASP CSRF Double-Submit Cookie:
        https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#double-submit-cookie
      - HTMX htmx:configRequest event (client wiring in csrf.js):
        https://htmx.org/events/#htmx:configRequest
    """

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send) -> None:
        from app.utils.csrf import generate_csrf_token, csrf_token_valid
        from starlette.datastructures import MutableHeaders
        import urllib.parse

        # Only intercept HTTP requests; pass websocket/lifespan through.
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # --- 1. Read or generate token from Cookie header ---
        # Parse cookies from raw ASGI scope headers — no Request object needed.
        cookie_header = ""
        for name, value in scope.get("headers", []):
            if name == b"cookie":
                cookie_header = value.decode("latin-1")
                break

        existing_token = ""
        for part in cookie_header.split(";"):
            k, _, v = part.strip().partition("=")
            if k.strip() == "csrf_token":
                existing_token = v.strip()
                break

        if existing_token:
            token = existing_token
            issued_new = False
        else:
            token = generate_csrf_token()
            issued_new = True

        # --- 2. Expose token on request.state (scope["state"]["csrf_token"]) ---
        # Starlette's Request.state property wraps scope["state"] dict; anything
        # set here is visible as request.state.csrf_token in downstream handlers.
        scope.setdefault("state", {})["csrf_token"] = token

        # --- 3. Enforcement gate ---
        under_pytest = os.getenv("PYTEST_CURRENT_TEST") is not None
        force_enforce = os.getenv("CSRF_FORCE_ENFORCE") == "1"
        should_enforce = (not under_pytest) or force_enforce

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/")

        # Determine content-type from headers
        content_type = ""
        for name, value in scope.get("headers", []):
            if name == b"content-type":
                content_type = value.decode("latin-1").lower()
                break

        # For unsafe methods under enforcement, we may need to buffer the body.
        # We ALWAYS replay the buffered body to downstream so route Form() params
        # are never starved, regardless of whether we read the form token from it.
        _safe_methods = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
        is_unsafe = method not in _safe_methods

        buffered_body: bytes | None = None
        replayed_receive = receive  # default: pass through unchanged

        if should_enforce and is_unsafe:
            # Read the header token first — if it's valid, no need to touch the body.
            header_token = ""
            for name, value in scope.get("headers", []):
                if name == b"x-csrftoken":
                    header_token = value.decode("latin-1")
                    break

            # Check if header token alone satisfies the CSRF gate.
            # (csrf_token_valid will return True for webhooks or safe methods too,
            # but we only reach here for enforcing + unsafe, so it checks the token.)
            header_passes = csrf_token_valid(method, path, token, header_token, "")

            if not header_passes:
                # Header token absent or invalid — must read body to find form token.
                # Buffer the full body from the ASGI receive stream.
                chunks: list[bytes] = []
                more_body = True
                while more_body:
                    msg = await receive()
                    chunks.append(msg.get("body", b""))
                    more_body = msg.get("more_body", False)
                buffered_body = b"".join(chunks)

                # Parse form token from urlencoded body only (multipart carries
                # the token via header instead, so we skip multipart parsing).
                form_token = ""
                if "application/x-www-form-urlencoded" in content_type:
                    try:
                        parsed = dict(
                            urllib.parse.parse_qsl(
                                buffered_body.decode("utf-8", errors="replace"),
                                keep_blank_values=True,
                            )
                        )
                        form_token = parsed.get("csrf_token", "")
                    except Exception:
                        form_token = ""

                if not csrf_token_valid(method, path, token, header_token, form_token):
                    # Return 403 as a raw ASGI response (no Request/Response objects).
                    # Fragmento con el mismo markup que partials/error_message.html.
                    # No se puede renderizar el parcial: esto corre en el ASGI
                    # crudo, sin Request ni Response. Se escribe a mano y se
                    # mantiene igual a proposito.
                    body = (
                        '<div class="error-msg error-msg--error" role="alert">'
                        "<span class=\"error-msg-text\">La sesi\u00f3n expir\u00f3 o el formulario "
                        "no es v\u00e1lido. Recarg\u00e1 la p\u00e1gina y prob\u00e1 de nuevo.</span>"
                        "</div>"
                    ).encode("utf-8")
                    await send({
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [
                            (b"content-type", b"text/html; charset=utf-8"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    })
                    await send({
                        "type": "http.response.body",
                        "body": body,
                        "more_body": False,
                    })
                    return

                # CSRF passed via form token — set up replay receive so downstream
                # route handlers can still read the full body.
                _body = buffered_body
                _replayed = False

                async def _replay_receive():
                    nonlocal _replayed
                    if not _replayed:
                        _replayed = True
                        return {"type": "http.request", "body": _body, "more_body": False}
                    return {"type": "http.disconnect"}

                replayed_receive = _replay_receive

            # header_passes == True: no body read needed, replayed_receive stays as `receive`

        # --- 4. Wrap send to inject Set-Cookie if we issued a new token ---
        if issued_new:
            secure_flag = os.getenv("PYTEST_CURRENT_TEST") is None
            # Build Set-Cookie header value (manual, no http.cookies overhead)
            max_age = 86400 * 7
            cookie_value = (
                f"csrf_token={token}; "
                f"Max-Age={max_age}; "
                f"Path=/; "
                f"SameSite=lax"
            )
            if secure_flag:
                cookie_value += "; Secure"
            # httponly is intentionally omitted — JS must read it for double-submit

            _cookie_bytes = cookie_value.encode("latin-1")

            async def _send_with_cookie(message):
                if message["type"] == "http.response.start":
                    # Inject Set-Cookie into the response headers
                    headers = MutableHeaders(scope=message)
                    headers.append("set-cookie", _cookie_bytes.decode("latin-1"))
                await send(message)

            await self._app(scope, replayed_receive, _send_with_cookie)
        else:
            await self._app(scope, replayed_receive, send)

app.add_middleware(CsrfMiddleware)

def _es_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _error_para_htmx(mensaje: str, status: int, correlacion: str | None = None):
    """Fragmento inline, no un documento entero.

    Sin esta rama, un 404 sobre un <tr> inyectaba un <!DOCTYPE html> completo
    adentro de la tabla. Reproducible desde user_row.html:18 con un id borrado.
    """
    if correlacion:
        mensaje = f"{mensaje} (código {correlacion})"
    return _templates.TemplateResponse(
        "partials/error_message.html",
        {"request": None, "message": mensaje, "level": "error"},
        status_code=status,
    )


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if _es_htmx(request):
        return _error_para_htmx("No se encontró lo que buscabas.", 404)
    return _templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "code": 404,
            "titulo": "Página no encontrada",
            "detalle": "La ruta que buscás no existe en el panel de Onnix SA.",
            "correlacion": None,
        },
        status_code=404,
    )


@app.exception_handler(500)
async def server_error(request: Request, exc):
    # Id corto para poder encontrar ESTE error en el log. Antes la pagina decia
    # «el equipo tecnico ya fue notificado», que era mentira: no hay Sentry ni
    # webhook. Ahora no promete nada y da con que buscar.
    correlacion = uuid.uuid4().hex[:8]
    logger.exception(
        "unhandled error correlacion=%s path=%s", correlacion, request.url.path
    )
    if _es_htmx(request):
        return _error_para_htmx(
            "Se rompió algo del lado del servidor.", 500, correlacion
        )
    return _templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "code": 500,
            "titulo": "Error interno del servidor",
            "detalle": "Se rompió algo del lado nuestro. Podés reintentar; si sigue, avisá.",
            "correlacion": correlacion,
        },
        status_code=500,
    )

@app.get("/")
async def root(request: Request):
    # Cada rol a su pantalla. `/dashboard` pide `require_admin` desde el
    # 2026-08-23, asi que mandar a todos ahi le devuelve un 403 al asesor que
    # escribe la URL pelada. La funcion vive en auth.py, que es donde ya decide
    # lo mismo despues del login: dos lugares que resuelven «adonde va este
    # usuario» son dos lugares que se van a contradecir.
    from app.routes.auth import _inicio_para

    return RedirectResponse(
        _inicio_para(request.session.get("user_role")), status_code=303,
    )

@app.get("/health")
async def health():
    # `revision` es el SHA con el que se buildeó la imagen. El pipeline lo compara
    # contra el label del contenedor y contra el commit que acaba de desplegar:
    # sin esto, un deploy que no reemplazó nada se ve idéntico a uno que sí.
    # Vacío cuando se buildea a mano sin GIT_SHA.
    return {"status": "ok", "revision": os.environ.get("APP_REVISION", "")}

@app.get("/health/scheduler")
async def health_scheduler(request: Request):
    """Return scheduler status and list of registered tasks."""
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None or not scheduler.is_running:
        return JSONResponse(
            {"status": "disabled", "tasks": []},
            status_code=200,
        )
    return JSONResponse(
        {
            "status": "running",
            "tasks": scheduler.list_tasks(),
        },
        status_code=200,
    )

"""Webhook unico de Meta — Instagram y Messenger entran por aca.

**Un solo endpoint para los tres canales, no tres.** La Graph API manda todo a
la misma URL y lo unico que cambia entre productos es el campo `object` del
cuerpo: `instagram`, `page` (Messenger) o `whatsapp_business_account`. El
handshake, la firma y el token de System User son identicos. Tres endpoints
serian tres copias del mismo codigo de verificacion, y en este repo lo escrito
tres veces ya divergio.

Hoy despacha Instagram y Messenger. WhatsApp sigue entrando por Twilio
(`webhooks/whatsapp.py`); cuando migre, entra por el mismo `_DESPACHO` de abajo
y no por un endpoint nuevo.

Dos cosas que el endpoint hace y una que NO:

1. **GET** responde el handshake de verificacion: Meta pega con
   `hub.mode=subscribe`, `hub.verify_token` y `hub.challenge`, y espera el
   challenge crudo si el token coincide.
2. **POST** valida `X-Hub-Signature-256` —HMAC-SHA256 del cuerpo CRUDO con el
   app secret— antes de mirar nada, persiste el entrante y lo empuja al panel
   por SSE.
3. **No contesta.** Onnix no tiene bot: el mensaje entra, aparece en la bandeja
   y responde una persona. Y todavia tampoco SALE nada por estos dos canales
   — `reply_service` lo corta explicito para que una respuesta de Instagram no
   termine saliendo por WhatsApp.

Sobre el 200: Meta reintenta cuando el endpoint no contesta 200, y reintenta el
lote entero. Un evento que no sabemos manejar tiene que contestar 200 igual, o
Meta lo reenvia en loop. Lo que NO puede contestar 200 es una firma invalida.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from app.bot.core.types import BotRequest
from app.config import settings as _settings

logger = logging.getLogger(__name__)

router = APIRouter()

# El `object` del cuerpo -> el canal con el que lo guardamos. Es el mismo
# vocabulario que `app.constants.CANALES` y que el CHECK de la migracion 047.
#
# `whatsapp_business_account` NO esta: WhatsApp entra por Twilio. Cuando migre,
# se agrega una linea aca y el resto del archivo no se toca.
_DESPACHO: dict[str, str] = {
    "instagram": "instagram",
    "page": "messenger",
}


def _get_app_secret() -> str:
    """El app secret con el que Meta firma cada cuerpo."""
    from app.bot.config import bot_settings
    return bot_settings.META_APP_SECRET


def _get_verify_token() -> str:
    """El token que se compara contra `hub.verify_token` en el handshake."""
    from app.bot.config import bot_settings
    return bot_settings.META_VERIFY_TOKEN


def verificar_firma(cuerpo: bytes, cabecera: str, app_secret: str) -> bool:
    """Valida `X-Hub-Signature-256` contra el cuerpo CRUDO.

    La cabecera viene como ``sha256=<hex>``. El HMAC se calcula sobre los bytes
    exactos que llegaron: si el cuerpo se parsea y se vuelve a serializar antes
    de firmar, cualquier diferencia de espacios o de orden de claves cambia el
    digest y la firma valida se rechaza.

    La comparacion es en tiempo constante — una comparacion normal filtra, por
    el tiempo que tarda en cortar, cuantos bytes del prefijo acerto quien
    prueba firmas.
    """
    if not cabecera.startswith("sha256="):
        return False
    esperado = hmac.new(
        app_secret.encode("utf-8"),
        cuerpo,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(esperado, cabecera[len("sha256="):])


def parsear_evento(cuerpo: dict) -> list[BotRequest]:
    """Extrae los mensajes entrantes de un lote de Meta.

    Un POST trae un LOTE: `entry` es una lista y cada entrada trae su propia
    lista `messaging`. Devolver una lista y no un solo request es la diferencia
    entre guardar todo y guardar el primero — Meta agrupa cuando llegan varios
    mensajes juntos, que es justo el caso de una rafaga.

    Se ignora en silencio, contestando 200:

    - un `object` que no despachamos (no es de este producto);
    - los ecos (`message.is_echo`), que son los mensajes que mandamos nosotros
      y volverian a entrar como si fueran del cliente;
    - los eventos que no son un mensaje —entregas, lecturas, reacciones,
      handover de hilo— que llegan por el mismo webhook;
    - un mensaje sin texto (audio, sticker, adjunto): todavia no hay de donde
      sacarle un cuerpo, y guardarlo vacio ensucia el hilo.
    """
    canal = _DESPACHO.get(cuerpo.get("object", ""))
    if canal is None:
        return []

    salida: list[BotRequest] = []
    for entrada in cuerpo.get("entry") or []:
        if not isinstance(entrada, dict):
            continue
        for evento in entrada.get("messaging") or []:
            if not isinstance(evento, dict):
                continue
            mensaje = evento.get("message")
            if not isinstance(mensaje, dict):
                continue  # entrega, lectura, reaccion, handover…
            if mensaje.get("is_echo"):
                continue  # lo mandamos nosotros
            texto = mensaje.get("text")
            if not isinstance(texto, str) or not texto.strip():
                continue  # sin texto no hay cuerpo que guardar
            remitente = (evento.get("sender") or {}).get("id")
            if not remitente:
                continue
            salida.append(
                BotRequest(
                    platform=canal,
                    # Los dos ids son el del USUARIO, no el de la pagina: es
                    # con lo unico que se lo puede volver a encontrar. En
                    # Instagram es el IGSID y en Messenger el PSID, y los dos
                    # son opacos y distintos por pagina.
                    chat_id=str(remitente),
                    user_id=str(remitente),
                    # Meta no manda el nombre en el webhook — hay que pedirlo a
                    # la Graph API. Hasta que haya salida, el hilo se muestra
                    # con el id y una persona le pone nombre desde el panel.
                    user_name="",
                    text=texto,
                    external_id=mensaje.get("mid"),
                )
            )
    return salida


async def _procesar(request: BotRequest) -> None:
    """Guarda el entrante y lo empuja al panel. Corre fuera del request.

    Espeja `_process_whatsapp`: el guardado va PRIMERO y con commit propio,
    porque el invariante es que un entrante valido se persiste pase lo que pase
    despues. El SSE es best-effort — que el panel no se entere al instante es
    molesto; que el mensaje no exista es perdida de datos.
    """
    from app.bot.core.conversation import persist_inbound
    from app.bot.observability.context import (
        set_request_context, clear_request_context,
    )
    from app.database import async_session_factory

    set_request_context(
        request_id=uuid.uuid4().hex,
        external_id=request.external_id or "",
        channel=request.platform,
        phone_e164=request.user_id or "unknown",
    )
    inicio = time.monotonic()
    session = async_session_factory()
    try:
        await persist_inbound(session, request)
        await session.commit()

        try:
            from sqlalchemy import text as _text
            from app.services.event_bus import event_bus as _event_bus
            _res = await session.execute(
                _text(
                    "SELECT c.id, co.id AS contact_id, co.name, co.phone, co.status, "
                    "  co.agent_user_id, "
                    "  (NOW() - co.created_at) < INTERVAL '30 seconds' AS is_new "
                    "FROM conversations c "
                    "JOIN contacts co ON co.id = c.contact_id "
                    "WHERE co.source = :source AND co.source_id = :source_id "
                    "ORDER BY c.updated_at DESC LIMIT 1"
                ),
                {"source": request.platform, "source_id": request.user_id},
            )
            _row = _res.first()
            if _row:
                _cid = _row.id
                await _event_bus.publish(
                    "conversation_update", {"conversation_id": _cid}
                )
                await _event_bus.publish(
                    f"message_update_{_cid}", {"conversation_id": _cid}
                )
                if _row.is_new:
                    await _event_bus.publish("lead.created", {
                        "contact_id": _row.contact_id,
                        "name": _row.name or "",
                        "source": request.platform,
                        "phone": _row.phone or "",
                        "status": _row.status or "new",
                        "agent_user_id": _row.agent_user_id,
                    })
        except Exception:
            pass  # SSE is best-effort

        logger.info(
            "Meta processing complete (%.0fms) — canal=%s sender=%s",
            (time.monotonic() - inicio) * 1000, request.platform, request.user_id,
        )
    except Exception:
        await session.rollback()
        logger.exception(
            "Meta processing error (%.0fms) — canal=%s sender=%s",
            (time.monotonic() - inicio) * 1000, request.platform, request.user_id,
        )
        try:
            from app.bot.services.error_service import BotErrorService
            err_session = async_session_factory()
            try:
                svc = BotErrorService(workflow=request.platform)
                await svc.record_error(
                    err_session, "meta webhook process failed",
                    node="webhook_process", chat_id=request.chat_id,
                )
            finally:
                await err_session.close()
        except Exception:
            logger.warning("Failed to record bot error (non-fatal)", exc_info=True)
    finally:
        await session.close()
        clear_request_context()


@router.get("/webhooks/meta")
async def verificar(request: Request) -> Response:
    """Handshake de verificacion.

    Meta espera el `hub.challenge` CRUDO —texto plano, sin comillas ni JSON— y
    solo si `hub.verify_token` coincide. Cualquier otra cosa es 403: contestar
    el challenge sin comparar el token deja que cualquiera suscriba su app a
    este endpoint.
    """
    params = request.query_params
    modo = params.get("hub.mode", "")
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge", "")

    esperado = _get_verify_token()
    if not esperado:
        if _settings.is_production:
            logger.error("META_VERIFY_TOKEN vacio en produccion — handshake rechazado")
            raise HTTPException(status_code=403, detail="Verification unavailable")
        logger.warning("META_VERIFY_TOKEN vacio — handshake rechazado (dev)")
        raise HTTPException(status_code=403, detail="Verification unavailable")

    if modo != "subscribe" or not hmac.compare_digest(token, esperado):
        logger.warning("Handshake de Meta rechazado: modo=%r token_ok=False", modo)
        raise HTTPException(status_code=403, detail="Verification failed")

    logger.info("Handshake de Meta verificado")
    return Response(content=challenge, media_type="text/plain")


@router.post("/webhooks/meta")
async def recibir(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Recibe el lote, valida la firma, encola y contesta 200.

    El cuerpo se lee CRUDO y se firma antes de parsearlo: la firma es sobre
    esos bytes exactos.
    """
    cuerpo = await request.body()

    app_secret = _get_app_secret()
    if app_secret:
        cabecera = request.headers.get("X-Hub-Signature-256", "")
        if not cabecera:
            logger.warning("Webhook de Meta sin X-Hub-Signature-256")
            raise HTTPException(status_code=403, detail="Missing signature")
        if not verificar_firma(cuerpo, cabecera, app_secret):
            logger.warning("Webhook de Meta con X-Hub-Signature-256 invalida")
            raise HTTPException(status_code=403, detail="Invalid signature")
    else:
        # Mismo fail-closed que el webhook de Twilio: en produccion, sin secreto
        # no se acepta nada. En dev se saltea para poder probar con un tunel.
        if _settings.is_production:
            raise HTTPException(
                status_code=403, detail="Signature verification unavailable",
            )
        logger.debug("META_APP_SECRET vacio — firma no verificada (dev mode)")

    try:
        datos = await request.json()
    except Exception:
        logger.warning("Webhook de Meta con cuerpo que no es JSON")
        # 200 igual: un cuerpo ilegible no mejora si Meta lo reintenta.
        return Response(status_code=200)

    if not isinstance(datos, dict):
        return Response(status_code=200)

    entrantes = parsear_evento(datos)
    if not entrantes:
        logger.debug(
            "Webhook de Meta sin mensajes utiles — object=%r", datos.get("object"),
        )
        return Response(status_code=200)

    for entrante in entrantes:
        background_tasks.add_task(_procesar, entrante)

    return Response(status_code=200)

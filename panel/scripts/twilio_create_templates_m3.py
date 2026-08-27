#!/usr/bin/env python3
# VENDORED COPY for in-container test parity (STAB-06 / TD-116-04).
# CANONICAL source is the repo-root /scripts/twilio_create_templates_m3.py.
# The Dockerfile COPYs this (panel build context) to /scripts/ so that
# tests/test_twilio_templates_m3_script.py collects in the panel container.
# Keep this file in sync with the canonical copy if the templates change.
"""
M3 — Crea 10 templates WhatsApp y los somete a Meta vía Twilio Content API.

Uso:
    python3 scripts/twilio_create_templates_m3.py --dry-run   # valida textos, sin HTTP
    python3 scripts/twilio_create_templates_m3.py --submit    # crea + submit real

Requiere env vars TWILIO_ACCOUNT_SID y TWILIO_AUTH_TOKEN.

Todos los templates se someten como category=MARKETING, language=es.

El script es IDEMPOTENTE: si una key ya tiene un ContentSid real en
bot_settings (valor que empieza con 'HX'), la saltea y loguea 'already has SID'.

NO correr en producción hasta que Ez valide los textos con la administradora.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# ── Configuración ──────────────────────────────────────────────────────────────

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

CONTENT_API = "https://content.twilio.com/v1/Content"
LOG_DIR = Path("/home/onnix/logs")
LOG_FILE = LOG_DIR / "templates_m3_submit.log"

# Timeout para llamadas HTTP a Twilio
HTTP_TIMEOUT = 30.0

# Pausa entre templates para no throttlear la API de Twilio/Meta
SLEEP_BETWEEN_TEMPLATES = 1.0

# ── Default vars por template (self-evident para Meta review) ─────────────────

_VARS_LEAD_MATCH = {
    "1": "la administradora",
    "2": "Departamento amoblado 3 dorm en Villa Morra",
    "3": "Asunción",
    "4": "USD 185.000",
}

_VARS_LEAD_REENVIADO = {
    "1": "Carlos",
    "2": "Departamento 2 dormitorios en Barrio Herrera",
    "3": "San Lorenzo",
    "4": "USD 85.000",
}

_VARS_SEND_PROPERTY = {
    "1": "María",
    "2": "Casa con jardín en Mburucuyá",
    "3": "Asunción",
    "4": "USD 120.000",
}

_VARS_SEND_PREFERENCES = {
    "1": "Roberto",
    "2": "departamento",
    "3": "Villa Morra",
    "4": "compra",
}

_VARS_GENERIC = {
    "1": "Laura",
}

_VARS_FOLLOWUP = {
    "1": "Jorge",
    "2": "casa en Fernando de la Mora",
    "3": "Fernando de la Mora",
    "4": "USD 95.000",
}

_VARS_AGENT_REPLY = {
    "1": "Paola",
    "2": "departamento",
    "3": "Asunción",
}

_VARS_RECURRENTE = {
    "1": "Daniel",
    "2": "Casa en Mburucuyá con jardín",
    "3": "Asunción",
    "4": "USD 120.000",
}

_VARS_RECURRENTE_REENVIADO = {
    "1": "Lusmily",
    "2": "Departamento 2 dormitorios en Barrio Herrera",
    "3": "San Lorenzo",
    "4": "USD 85.000",
}

# ── Definición de los 10 templates ────────────────────────────────────────────
# Cada entrada es un dict con:
#   key          → clave en bot_settings
#   name         → nombre del template en Meta (snake_case, ≤ 512 chars, único)
#   friendly_name → nombre interno en Twilio (= name)
#   language     → siempre "es"
#   category     → siempre "MARKETING"
#   body         → texto con {{N}} vars
#   buttons      → lista de dicts {title, id} o lista vacía si no hay botones
#   variables    → dict de defaults self-evident para Meta review
#
# TEXTOS DEFINIDOS POR EZ — editar en reunión con la administradora antes de --submit.

TEMPLATES: list[dict[str, Any]] = [
    # ── 1. IC lead directo ────────────────────────────────────────────────────
    {
        "key": "wa_tpl_ic_welcome_v3",
        "name": "onnix_ic_welcome_v3",
        "friendly_name": "onnix_ic_welcome_v3",
        "language": "es",
        "category": "MARKETING",
        "body": (
            "\U0001f3e0 \u00a1Hola {{1}}! Soy Onnix, el asistente virtual de "
            "Onnix\u00a0SA.\n"
            "Vimos tu consulta en InfoCasas sobre:\n\n"
            "\U0001f3f7\ufe0f {{2}}\n"
            "\U0001f4cd {{3}}\n"
            "\U0001f4b0 {{4}}\n\n"
            "\u00bfQuer\u00e9s ver todos los detalles o que un asesor te contacte "
            "para coordinar una visita?"
        ),
        "buttons": [
            {"title": "Ver detalles", "id": "view_details"},
            {"title": "Hablar con un asesor", "id": "talk_to_agent"},
        ],
        "variables": _VARS_LEAD_MATCH,
    },
    # ── 2. IC lead reenviado ──────────────────────────────────────────────────
    {
        "key": "wa_tpl_ic_reenviado_welcome_v3",
        "name": "onnix_ic_reenviado_welcome_v3",
        "friendly_name": "onnix_ic_reenviado_welcome_v3",
        "language": "es",
        "category": "MARKETING",
        "body": (
            "\U0001f3e0 \u00a1Hola {{1}}! Soy Onnix, el asistente virtual de "
            "Onnix\u00a0SA.\n"
            "Vimos que consultaste una propiedad en InfoCasas. Tenemos esta "
            "opci\u00f3n parecida en nuestro portafolio que quiz\u00e1s te sirva:\n\n"
            "\U0001f3f7\ufe0f {{2}}\n"
            "\U0001f4cd {{3}}\n"
            "\U0001f4b0 {{4}}\n\n"
            "\u00bfQuer\u00e9s ver todos los detalles o que un asesor te contacte?"
        ),
        "buttons": [
            {"title": "Ver detalles", "id": "view_details"},
            {"title": "Hablar con un asesor", "id": "talk_to_agent"},
        ],
        "variables": _VARS_LEAD_REENVIADO,
    },
    # ── 3. Asesor envía propiedad desde panel ─────────────────────────────────
    {
        "key": "wa_tpl_send_property_v4",
        "name": "onnix_send_property_v4",
        "friendly_name": "onnix_send_property_v4",
        "language": "es",
        "category": "MARKETING",
        "body": (
            "\U0001f3e0 \u00a1Hola {{1}}! Te escribimos desde Onnix\u00a0SA.\n\n"
            "Encontramos una propiedad que se ajusta a lo que est\u00e1s buscando:\n\n"
            "\U0001f3f7\ufe0f {{2}}\n"
            "\U0001f4cd {{3}}\n"
            "\U0001f4b0 {{4}}\n\n"
            "Respond\u00e9 este mensaje y con gusto te damos m\u00e1s detalles o "
            "coordinamos una visita."
        ),
        "buttons": [],
        "variables": _VARS_SEND_PROPERTY,
    },
    # ── 4. Asesor envía por preferencias ─────────────────────────────────────
    {
        "key": "wa_tpl_send_preferences_v4",
        "name": "onnix_send_preferences_v4",
        "friendly_name": "onnix_send_preferences_v4",
        "language": "es",
        "category": "MARKETING",
        "body": (
            "\U0001f3e0 \u00a1Hola {{1}}! Te escribimos desde Onnix\u00a0SA.\n\n"
            "Tenemos opciones de {{2}} en {{3}} para {{4}} que pueden interesarte.\n\n"
            "Respond\u00e9 este mensaje y te compartimos las opciones disponibles "
            "o coordinamos una consulta con nuestro equipo."
        ),
        "buttons": [],
        "variables": _VARS_SEND_PREFERENCES,
    },
    # ── 5. Contacto nuevo sin contexto ────────────────────────────────────────
    {
        "key": "wa_tpl_send_generic_v3",
        "name": "onnix_send_generic_v3",
        "friendly_name": "onnix_send_generic_v3",
        "language": "es",
        "category": "MARKETING",
        "body": (
            "\U0001f3e0 \u00a1Hola {{1}}! Soy Onnix, el asistente virtual de "
            "Onnix\u00a0SA.\n\n"
            "Estamos para ayudarte a encontrar tu propiedad ideal en Paraguay. "
            "\u00bfEst\u00e1s buscando para comprar o alquilar?"
        ),
        "buttons": [
            {"title": "Comprar", "id": "intent_comprar"},
            {"title": "Alquilar", "id": "intent_alquilar"},
        ],
        "variables": _VARS_GENERIC,
    },
    # ── 6. Follow-up 24h ──────────────────────────────────────────────────────
    {
        "key": "wa_tpl_followup_v3",
        "name": "onnix_followup_v3",
        "friendly_name": "onnix_followup_v3",
        "language": "es",
        "category": "MARKETING",
        "body": (
            "\U0001f3e0 \u00a1Hola {{1}}! Soy Onnix de Onnix\u00a0SA.\n\n"
            "Hace un d\u00eda te mostramos un {{2}} en {{3}} a {{4}}.\n\n"
            "\u00bfSegu\u00eds interesado/a? Estamos para ayudarte cuando quieras."
        ),
        "buttons": [
            {"title": "Ver esta propiedad", "id": "followup_view"},
        ],
        "variables": _VARS_FOLLOWUP,
    },
    # ── 7. Follow-up 72h ──────────────────────────────────────────────────────
    {
        "key": "wa_tpl_followup_72h_v3",
        "name": "onnix_followup_72h_v3",
        "friendly_name": "onnix_followup_72h_v3",
        "language": "es",
        "category": "MARKETING",
        "body": (
            "\U0001f3e0 \u00a1Hola {{1}}! Te recuerda Onnix de Onnix\u00a0SA.\n\n"
            "Hace unos d\u00edas te mostramos un {{2}} en {{3}} a {{4}}.\n\n"
            "Si ten\u00e9s alguna pregunta o quer\u00e9s ver otras opciones, respond\u00e9 "
            "este mensaje. \u00a1Con gusto te ayudamos!"
        ),
        "buttons": [
            {"title": "Ver esta propiedad", "id": "followup_72h_view"},
        ],
        "variables": _VARS_FOLLOWUP,
    },
    # ── 8. Reactivación por asesor ────────────────────────────────────────────
    {
        "key": "wa_tpl_agent_reply_v3",
        "name": "onnix_agent_reply_v3",
        "friendly_name": "onnix_agent_reply_v3",
        "language": "es",
        "category": "MARKETING",
        "body": (
            "\U0001f3e0 \u00a1Hola {{1}}! Te escribe el equipo de "
            "Onnix\u00a0SA.\n\n"
            "Ten\u00edamos pendiente tu consulta sobre {{2}} en {{3}}.\n\n"
            "\u00bfSeg\u00fds buscando? Respond\u00e9 este mensaje y te ayudamos "
            "a encontrar la propiedad ideal."
        ),
        "buttons": [],
        "variables": _VARS_AGENT_REPLY,
    },
    # ── 9. IC recurrente directo ──────────────────────────────────────────────
    {
        "key": "wa_tpl_ic_recurrente_directo_v2",
        "name": "onnix_ic_recurrente_directo_v2",
        "friendly_name": "onnix_ic_recurrente_directo_v2",
        "language": "es",
        "category": "MARKETING",
        "body": (
            "\U0001f3e0 \u00a1Hola de nuevo {{1}}!\n"
            "Vimos que consultaste una nueva propiedad en InfoCasas:\n\n"
            "\U0001f3f7\ufe0f {{2}}\n"
            "\U0001f4cd {{3}}\n"
            "\U0001f4b0 {{4}}\n\n"
            "\u00bfQuer\u00e9s ver los detalles o hablo con un asesor para que "
            "te contacte?"
        ),
        "buttons": [],
        "variables": _VARS_RECURRENTE,
    },
    # ── 10. IC recurrente reenviado ───────────────────────────────────────────
    {
        "key": "wa_tpl_ic_recurrente_reenviado_v2",
        "name": "onnix_ic_recurrente_reenviado_v2",
        "friendly_name": "onnix_ic_recurrente_reenviado_v2",
        "language": "es",
        "category": "MARKETING",
        "body": (
            "\U0001f3e0 \u00a1Hola de nuevo {{1}}!\n"
            "InfoCasas nos comparti\u00f3 tu nueva consulta. Tenemos esta "
            "opci\u00f3n que podr\u00eda interesarte:\n\n"
            "\U0001f3f7\ufe0f {{2}}\n"
            "\U0001f4cd {{3}}\n"
            "\U0001f4b0 {{4}}\n\n"
            "\u00bfQuer\u00e9s m\u00e1s detalles o te paso con el equipo?"
        ),
        "buttons": [],
        "variables": _VARS_RECURRENTE_REENVIADO,
    },
]

# Mapa key → (name, description) para la migración — usado también por el
# script de actualización de SIDs.
M3_KEYS: list[str] = [t["key"] for t in TEMPLATES]

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("m3_templates")


def _log_to_file(record: dict[str, Any]) -> None:
    """Append a JSON line to the submit log."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("Could not write to log file %s: %s", LOG_FILE, exc)


# ── Validaciones ──────────────────────────────────────────────────────────────

MAX_BODY_CHARS = 1024
MAX_BUTTON_TITLE_CHARS = 20
MAX_BUTTONS = 3


def validate_templates() -> list[str]:
    """Validate all templates in TEMPLATES.  Return list of error messages."""
    errors: list[str] = []

    for tpl in TEMPLATES:
        key = tpl["key"]
        body: str = tpl["body"]
        buttons: list[dict] = tpl.get("buttons", [])

        if len(body) > MAX_BODY_CHARS:
            errors.append(
                f"[{key}] body too long: {len(body)} chars (max {MAX_BODY_CHARS})"
            )

        if len(buttons) > MAX_BUTTONS:
            errors.append(
                f"[{key}] too many buttons: {len(buttons)} (max {MAX_BUTTONS})"
            )

        for btn in buttons:
            title = btn.get("title", "")
            if len(title) > MAX_BUTTON_TITLE_CHARS:
                errors.append(
                    f"[{key}] button title too long: '{title}' "
                    f"({len(title)} chars, max {MAX_BUTTON_TITLE_CHARS})"
                )

        if tpl.get("category") != "MARKETING":
            errors.append(f"[{key}] category must be MARKETING, got: {tpl.get('category')!r}")

        if tpl.get("language") != "es":
            errors.append(f"[{key}] language must be 'es', got: {tpl.get('language')!r}")

    # Check for duplicate keys
    seen_keys: set[str] = set()
    for tpl in TEMPLATES:
        k = tpl["key"]
        if k in seen_keys:
            errors.append(f"Duplicate key in TEMPLATES: {k!r}")
        seen_keys.add(k)

    return errors


# ── Payload builder ───────────────────────────────────────────────────────────

def _build_content_payload(tpl: dict[str, Any]) -> dict[str, Any]:
    """Build the payload for POST /v1/Content."""
    buttons = tpl.get("buttons", [])

    types: dict[str, Any] = {}

    if buttons:
        # Twilio quick-reply type for interactive buttons
        types["twilio/quick-reply"] = {
            "body": tpl["body"],
            "actions": [
                {"type": "QUICK_REPLY", "title": btn["title"], "id": btn["id"]}
                for btn in buttons
            ],
        }
        # Always include twilio/text as fallback for non-interactive channels
        types["twilio/text"] = {"body": tpl["body"]}
    else:
        types["twilio/text"] = {"body": tpl["body"]}

    return {
        "friendly_name": tpl["friendly_name"],
        "language": tpl["language"],
        "variables": tpl.get("variables", {}),
        "types": types,
    }


# ── Idempotency: check bot_settings ──────────────────────────────────────────

def _get_existing_sids() -> dict[str, str]:
    """Return {key: value} for M3 keys that already have a real SID in bot_settings.

    Returns empty dict if DATABASE_URL is not set (safe for dry-run).
    """
    if not DATABASE_URL:
        return {}

    try:
        import psycopg  # type: ignore[import]

        with psycopg.connect(DATABASE_URL) as conn:
            keys_sql = ", ".join(f"'{k}'" for k in M3_KEYS)
            rows = conn.execute(
                f"SELECT key, value FROM bot_settings WHERE key IN ({keys_sql})"
            ).fetchall()
        return {row[0]: row[1] for row in rows}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not query bot_settings (skipping idempotency check): %s", exc)
        return {}


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _create_content(client: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to /v1/Content and return parsed JSON."""
    resp = client.post(CONTENT_API, json=payload, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _submit_approval(
    client: httpx.Client, sid: str, meta_name: str, category: str
) -> dict[str, Any]:
    """POST to /v1/Content/{sid}/ApprovalRequests/whatsapp."""
    url = f"{CONTENT_API}/{sid}/ApprovalRequests/whatsapp"
    resp = client.post(
        url,
        json={"name": meta_name, "category": category},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ── Dry-run mode ──────────────────────────────────────────────────────────────

def run_dry_run() -> int:
    """Validate and print all template payloads.  Return exit code."""
    print("=" * 70)
    print("DRY-RUN — M3 WhatsApp Templates (10 templates)")
    print("=" * 70)

    errors = validate_templates()
    if errors:
        print("\nVALIDATION ERRORS:")
        for err in errors:
            print(f"  ERROR: {err}")
        return 1

    print(f"\nAll {len(TEMPLATES)} templates pass validation.\n")

    for i, tpl in enumerate(TEMPLATES, 1):
        payload = _build_content_payload(tpl)
        print(f"── Template {i:2d}: {tpl['key']}")
        print(f"   name       : {tpl['name']}")
        print(f"   category   : {tpl['category']}")
        print(f"   body length: {len(tpl['body'])} chars")
        print(f"   buttons    : {len(tpl.get('buttons', []))}")
        print(f"\n   BODY:\n{tpl['body']}\n")
        print("   PAYLOAD (JSON):")
        print(json.dumps(payload, indent=4, ensure_ascii=False))
        print()

    print("=" * 70)
    print("Dry-run complete. No HTTP calls made.")
    print("Review texts above with la administradora, then run --submit.")
    print("=" * 70)
    return 0


# ── Submit mode ───────────────────────────────────────────────────────────────

def run_submit() -> int:
    """Create + submit all 10 templates to Meta via Twilio.  Return exit code."""
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        logger.error(
            "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set. "
            "export them before running --submit."
        )
        return 1

    errors = validate_templates()
    if errors:
        logger.error("Validation failed — fix before submitting:")
        for err in errors:
            logger.error("  %s", err)
        return 1

    # Idempotency: skip keys that already have a real SID
    existing_sids = _get_existing_sids()

    results: list[dict[str, Any]] = []

    with httpx.Client(
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        headers={"Content-Type": "application/json"},
    ) as client:
        for i, tpl in enumerate(TEMPLATES, 1):
            key = tpl["key"]
            logger.info("[%d/%d] Processing: %s", i, len(TEMPLATES), key)

            # Idempotency check
            existing_value = existing_sids.get(key, "")
            if existing_value and existing_value.startswith("HX"):
                msg = f"  already has SID: {existing_value} — skipping"
                logger.info(msg)
                results.append({"key": key, "status": "skipped", "sid": existing_value})
                _log_to_file({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "key": key,
                    "status": "skipped",
                    "existing_sid": existing_value,
                })
                continue

            payload = _build_content_payload(tpl)

            try:
                # Step 1: Create Content
                logger.info("  Creating content...")
                created = _create_content(client, payload)
                sid: str = created.get("sid", "")
                if not sid:
                    logger.error("  No SID in response: %s", json.dumps(created))
                    results.append({"key": key, "status": "error", "error": "no_sid"})
                    continue

                logger.info("  Created SID: %s", sid)

                # Step 2: Submit approval
                logger.info("  Submitting for Meta approval (MARKETING)...")
                approval = _submit_approval(client, sid, tpl["name"], tpl["category"])
                approval_status = approval.get("approval_status", {}).get("whatsapp", {})
                status = approval_status.get("status", "unknown")
                logger.info("  Approval status: %s", status)

                record = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "key": key,
                    "name": tpl["name"],
                    "sid": sid,
                    "approval_status": status,
                    "approval_raw": approval_status,
                }
                results.append({"key": key, "status": "submitted", "sid": sid, "approval": status})
                _log_to_file(record)

            except httpx.HTTPStatusError as exc:
                logger.error(
                    "  HTTP error %d for %s: %s",
                    exc.response.status_code,
                    key,
                    exc.response.text,
                )
                results.append({
                    "key": key,
                    "status": "http_error",
                    "code": exc.response.status_code,
                    "body": exc.response.text,
                })
                _log_to_file({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "key": key,
                    "status": "http_error",
                    "code": exc.response.status_code,
                })
            except Exception as exc:  # noqa: BLE001
                logger.error("  Unexpected error for %s: %s", key, exc)
                results.append({"key": key, "status": "error", "error": str(exc)})
                _log_to_file({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "key": key,
                    "status": "error",
                    "error": str(exc),
                })

            if i < len(TEMPLATES):
                time.sleep(SLEEP_BETWEEN_TEMPLATES)

    # Summary
    print("\n" + "=" * 70)
    print("SUBMIT SUMMARY")
    print("=" * 70)
    for r in results:
        sid_display = r.get("sid", "—")
        status = r.get("status", "?")
        print(f"  {r['key']:<42} {status:<12} {sid_display}")

    submitted = [r for r in results if r["status"] == "submitted"]
    skipped = [r for r in results if r["status"] == "skipped"]
    errors_list = [r for r in results if r["status"] not in ("submitted", "skipped")]

    print(f"\nSubmitted: {len(submitted)} | Skipped: {len(skipped)} | Errors: {len(errors_list)}")
    print(f"Log: {LOG_FILE}")
    print("=" * 70)

    if submitted:
        print("\nNext step (24-48h after Meta review):")
        print("  python3 scripts/twilio_update_m3_sids.py --commit")

    return 0 if not errors_list else 1


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="M3 — Crea y somete 10 templates WhatsApp a Meta vía Twilio."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida textos e imprime payloads. Sin HTTP. Ideal para revisar con la administradora.",
    )
    group.add_argument(
        "--submit",
        action="store_true",
        help="Crea + somete los templates a Meta. Requiere TWILIO_ACCOUNT_SID y TWILIO_AUTH_TOKEN.",
    )
    args = parser.parse_args()

    if args.dry_run:
        sys.exit(run_dry_run())
    else:
        sys.exit(run_submit())


if __name__ == "__main__":
    main()

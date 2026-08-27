#!/usr/bin/env python3
"""
Consulta estado de aprobación de los 10 templates M3 en Meta vía Twilio y,
para los aprobados, sincroniza su ContentSid a bot_settings.

Uso:
    python3 scripts/twilio_update_m3_sids.py           # solo reporta (dry-run)
    python3 scripts/twilio_update_m3_sids.py --commit  # actualiza bot_settings

Sin --commit: solo imprime el estado de cada template. Seguro de correr N veces.
Con --commit: hace UPDATE en bot_settings solo para los templates aprobados.

Requiere:
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN  — credenciales Twilio
    DATABASE_URL                            — conexión a la DB (si --commit)

Los SIDs se leen del log de submit (/home/onnix/logs/templates_m3_submit.log).
Si el log no existe, intenta descubrir los SIDs via GET /v1/Content filtrando
por friendly_name que empiece con "onnix_".

Correr 24-48h después de `scripts/twilio_create_templates_m3.py --submit`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# ── Configuración ──────────────────────────────────────────────────────────────

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

CONTENT_API = "https://content.twilio.com/v1/Content"
LOG_FILE = Path("/home/onnix/logs/templates_m3_submit.log")
HTTP_TIMEOUT = 30.0

# ── M3 template keys (espejo de twilio_create_templates_m3.py) ────────────────
# Importar desde el script de creación es más limpio, pero para independencia
# del módulo los definimos aquí también.  Si cambian, actualizar ambos archivos.

M3_TEMPLATE_DEFINITIONS: list[dict[str, str]] = [
    {"key": "wa_tpl_ic_welcome_v3",              "name": "onnix_ic_welcome_v3"},
    {"key": "wa_tpl_ic_reenviado_welcome_v3",     "name": "onnix_ic_reenviado_welcome_v3"},
    {"key": "wa_tpl_send_property_v4",            "name": "onnix_send_property_v4"},
    {"key": "wa_tpl_send_preferences_v4",         "name": "onnix_send_preferences_v4"},
    {"key": "wa_tpl_send_generic_v3",             "name": "onnix_send_generic_v3"},
    {"key": "wa_tpl_followup_v3",                 "name": "onnix_followup_v3"},
    {"key": "wa_tpl_followup_72h_v3",             "name": "onnix_followup_72h_v3"},
    {"key": "wa_tpl_agent_reply_v3",              "name": "onnix_agent_reply_v3"},
    {"key": "wa_tpl_ic_recurrente_directo_v2",    "name": "onnix_ic_recurrente_directo_v2"},
    {"key": "wa_tpl_ic_recurrente_reenviado_v2",  "name": "onnix_ic_recurrente_reenviado_v2"},
]

M3_KEYS: list[str] = [d["key"] for d in M3_TEMPLATE_DEFINITIONS]
_NAME_TO_KEY: dict[str, str] = {d["name"]: d["key"] for d in M3_TEMPLATE_DEFINITIONS}

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("m3_update_sids")


# ── Leer SIDs del log de submit ───────────────────────────────────────────────

def _load_sids_from_log() -> dict[str, str]:
    """Return {key: sid} from the submit log (last entry per key).

    El log de submit guarda un record por cada template creado con campos
    `key`, `sid`, `approval_status`. Tomamos el último SID por key; si hubo
    rerun, el más reciente gana.
    """
    if not LOG_FILE.exists():
        return {}

    sids: dict[str, str] = {}
    with LOG_FILE.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = record.get("key", "")
            sid = record.get("sid", "")
            if key and sid and sid.startswith("HX"):
                sids[key] = sid  # último gana por orden de aparición

    return sids


# ── Descubrir SIDs vía API de Twilio ─────────────────────────────────────────

def _discover_sids_from_api(client: httpx.Client) -> dict[str, str]:
    """Fetch all Content resources and match by friendly_name prefix 'onnix_'.

    Returns {key: sid}.
    """
    logger.info("Querying Twilio Content API to discover SIDs...")
    sids: dict[str, str] = {}
    page_url: str | None = f"{CONTENT_API}?PageSize=200"

    while page_url:
        resp = client.get(page_url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        for content in data.get("contents", []):
            name: str = content.get("friendly_name", "")
            sid: str = content.get("sid", "")
            if name.startswith("onnix_") and name in _NAME_TO_KEY:
                bot_key = _NAME_TO_KEY[name]
                sids[bot_key] = sid
                logger.info("  Found: %s -> %s", bot_key, sid)

        # Pagination
        meta = data.get("meta", {})
        next_url = meta.get("next_page_url")
        page_url = next_url if next_url else None

    return sids


# ── Consultar estado de aprobación ────────────────────────────────────────────

def _get_approval_status(
    client: httpx.Client, sid: str
) -> dict[str, Any]:
    """GET /Content/{sid}/ApprovalRequests and return the whatsapp status dict."""
    url = f"{CONTENT_API}/{sid}/ApprovalRequests"
    resp = client.get(url, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    # The WhatsApp approval is nested under approval_requests[*].approval_status.whatsapp
    # or directly as whatsapp key depending on API version.
    # Twilio returns: {"approval_requests": [{"name": ..., "approval_status": {...}}]}
    approval_requests = data.get("approval_requests") or []
    for req in approval_requests:
        approval_status = req.get("approval_status", {})
        if "whatsapp" in approval_status:
            return approval_status["whatsapp"]
    # Fallback: some versions return top-level
    return data.get("whatsapp", data.get("approval_status", {}))


# ── DB update ─────────────────────────────────────────────────────────────────

def _update_bot_setting(key: str, sid: str) -> None:
    """UPDATE bot_settings SET value=sid WHERE key=key."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set — cannot update bot_settings.")

    import psycopg  # type: ignore[import]

    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            "UPDATE bot_settings SET value = %s, updated_at = NOW() WHERE key = %s",
            (sid, key),
        )
        conn.commit()


def _get_current_bot_settings() -> dict[str, str]:
    """Return {key: value} for M3 keys from bot_settings."""
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
        logger.warning("Could not query bot_settings: %s", exc)
        return {}


# ── Main logic ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Consulta aprobación M3 templates en Meta y sincroniza SIDs a bot_settings."
        )
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        default=False,
        help="Actualiza bot_settings para los templates aprobados. Sin este flag: solo reporta.",
    )
    args = parser.parse_args()

    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        logger.error("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set.")
        sys.exit(1)

    if args.commit and not DATABASE_URL:
        logger.error("DATABASE_URL must be set when using --commit.")
        sys.exit(1)

    current_settings = _get_current_bot_settings()

    with httpx.Client(
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
    ) as client:
        # Try log first, fall back to API discovery
        sids = _load_sids_from_log()
        if not sids:
            logger.info("No submit log found — discovering SIDs via Twilio API.")
            sids = _discover_sids_from_api(client)

        if not sids:
            logger.error(
                "No SIDs found. Run scripts/twilio_create_templates_m3.py --submit first."
            )
            sys.exit(1)

        logger.info("Found %d SIDs from log/API.", len(sids))

        # Check approval status for each
        results: list[dict[str, Any]] = []

        for tpl_def in M3_TEMPLATE_DEFINITIONS:
            key = tpl_def["key"]
            name = tpl_def["name"]

            current_value = current_settings.get(key, "PLACEHOLDER")
            if current_value.startswith("HX"):
                logger.info("[%s] already has SID in bot_settings: %s", key, current_value)
                results.append({
                    "key": key,
                    "name": name,
                    "status": "already_set",
                    "sid": current_value,
                })
                continue

            sid = sids.get(key)
            if not sid:
                logger.warning("[%s] No SID found — template may not have been submitted.", key)
                results.append({
                    "key": key,
                    "name": name,
                    "status": "no_sid",
                    "sid": None,
                })
                continue

            try:
                logger.info("[%s] Checking approval status for SID %s ...", key, sid)
                approval = _get_approval_status(client, sid)
                status = approval.get("status", "unknown")
                rejection_reason = approval.get("rejection_reason") or approval.get("rejection_reasons")

                logger.info("  Status: %s", status)
                if rejection_reason:
                    logger.warning("  Rejection reason: %s", rejection_reason)

                result_entry: dict[str, Any] = {
                    "key": key,
                    "name": name,
                    "sid": sid,
                    "status": status,
                    "approval_raw": approval,
                }
                if rejection_reason:
                    result_entry["rejection_reason"] = rejection_reason

                if status == "approved":
                    if args.commit:
                        logger.info("  --commit: updating bot_settings[%s] = %s", key, sid)
                        _update_bot_setting(key, sid)
                        result_entry["committed"] = True
                    else:
                        logger.info("  APPROVED — run with --commit to sync to bot_settings.")
                        result_entry["committed"] = False

                results.append(result_entry)

            except httpx.HTTPStatusError as exc:
                logger.error(
                    "  HTTP error %d checking %s: %s",
                    exc.response.status_code,
                    key,
                    exc.response.text,
                )
                results.append({
                    "key": key,
                    "name": name,
                    "sid": sid,
                    "status": "http_error",
                    "code": exc.response.status_code,
                })

    # ── Print summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"M3 TEMPLATE APPROVAL STATUS — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Mode: {'--commit (updates DB)' if args.commit else 'dry-run (report only)'}")
    print("=" * 70)

    status_counts: dict[str, int] = {}
    for r in results:
        s = r.get("status", "?")
        status_counts[s] = status_counts.get(s, 0) + 1
        sid_display = r.get("sid") or "—"
        committed = " [COMMITTED]" if r.get("committed") else ""
        rejection = (
            f" — rejection: {r.get('rejection_reason')}"
            if r.get("rejection_reason")
            else ""
        )
        print(f"  {r['key']:<42} {s:<12} {sid_display}{committed}{rejection}")

    print("\nSummary:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    approved_count = status_counts.get("approved", 0)
    if approved_count > 0 and not args.commit:
        print(f"\n{approved_count} template(s) approved — run with --commit to sync SIDs to bot_settings.")

    print("=" * 70)


if __name__ == "__main__":
    main()

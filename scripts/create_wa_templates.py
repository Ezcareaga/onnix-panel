"""Create Twilio Content Templates for dynamic WA buttons.

Run once to create templates, then save the ContentSids to bot_settings.
Usage: python scripts/create_wa_templates.py

Creates two new templates:
- onnix_res2_con_pendientes: 2 results + "Mas opciones" button
- onnix_res1_con_asesor: 1 result + "Hablar con asesor" button
"""
import os
import sys

import httpx

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")

TEMPLATES = [
    {
        "friendly_name": "onnix_res2_con_pendientes",
        "language": "es",
        "variables": {"1": "Resultados de b\u00fasqueda"},
        "types": {
            "twilio/quick-reply": {
                "body": "{{1}}",
                "actions": [
                    {"id": "detail_1", "title": "1\ufe0f\u20e3 Ver detalle"},
                    {"id": "detail_2", "title": "2\ufe0f\u20e3 Ver detalle"},
                    {"id": "ver_mas", "title": "\u27a1\ufe0f M\u00e1s opciones"},
                ],
            }
        },
    },
    {
        "friendly_name": "onnix_res1_con_asesor",
        "language": "es",
        "variables": {"1": "Resultado de b\u00fasqueda"},
        "types": {
            "twilio/quick-reply": {
                "body": "{{1}}",
                "actions": [
                    {"id": "detail_1", "title": "1\ufe0f\u20e3 Ver detalle"},
                    {"id": "hablar_asesor", "title": "\U0001f4ac Hablar c/ asesor"},
                ],
            }
        },
    },
]


def main():
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        print("ERROR: TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set in environment")
        sys.exit(1)

    for tpl in TEMPLATES:
        resp = httpx.post(
            "https://content.twilio.com/v1/Content",
            json=tpl,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            bot_key = tpl["friendly_name"].replace("onnix_", "")
            print(f"Created: {tpl['friendly_name']} -> ContentSid: {data['sid']}")
            print(f"  INSERT INTO bot_settings (key, value) VALUES ('wa_tpl_{bot_key}', '{data['sid']}');")
        else:
            print(f"FAILED: {tpl['friendly_name']} -- {resp.status_code}: {resp.text}")


if __name__ == "__main__":
    main()

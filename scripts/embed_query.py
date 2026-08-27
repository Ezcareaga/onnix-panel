#!/usr/bin/env python3
"""
Genera embedding 768-dim para una query de texto usando Gemini embedding-001.
Uso: python3 embed_query.py "departamento 2 dormitorios en Asuncion"
Salida: JSON array a stdout. Array vacio en caso de error.

Llamado desde N8N Code nodes via child_process.execSync().
"""
import json
import sys
import urllib.request
import urllib.error

GEMINI_API_KEY = ""
for line in open("/home/onnix/.env"):
    if line.startswith("GEMINI_API_KEY="):
        GEMINI_API_KEY = line.split("=", 1)[1].strip()
        break

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-embedding-001:embedContent?key={GEMINI_API_KEY}"
)
TIMEOUT = 10


def get_embedding(text):
    """Call Gemini embedding-001 and return 768-dim vector."""
    body = {
        "model": "models/gemini-embedding-001",
        "content": {"parts": [{"text": text}]},
        "outputDimensionality": 768,
    }
    req = urllib.request.Request(
        GEMINI_URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        result = json.loads(resp.read())
    return result["embedding"]["values"]


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("[]")
        return

    query = sys.argv[1].strip()
    # Truncate to ~8000 chars (~2000 tokens) to stay within limits
    if len(query) > 8000:
        query = query[:8000]

    try:
        embedding = get_embedding(query)
        print(json.dumps(embedding))
    except Exception:
        print("[]")


if __name__ == "__main__":
    main()

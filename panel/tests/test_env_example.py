"""`.env.example` contra lo que el sistema exige de verdad.

Este archivo es lo primero que abre alguien que levanta el proyecto de cero, y
lo que se le olvida no falla ahi: falla mas tarde y con otra cara. Medido el
2026-08-23, tenia **16 variables muertas y 6 vivas sin listar**:

  - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` y `TWILIO_WHATSAPP_NUMBER` — las
    tres las pide `docker-compose.yml` y ninguna estaba. `TWILIO_AUTH_TOKEN`
    ademas es fail-closed en produccion: sin ella el panel **no arranca**, y el
    propio `.env.example` nombraba la variable en un comentario sin listarla.
  - `TELEGRAM_WEBHOOK_SECRET` — el otro fail-closed, con la misma historia.
  - `GEMINI_API_KEY` — va vacia a proposito, pero tiene que estar.
  - `TEST_ADMIN_PASSWORD` — sin ella ~400 tests fallan con 303 y parece que la
    app esta rota.

Y arrastraba 11 variables de N8N —descomisionado— mas 3 de Cloudinary, que no
aparece en una sola linea del repo.

**El test compara contra el compose, no contra una lista escrita a mano.** Una
lista a mano es otra copia que diverge; el compose es el que rompe el arranque.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[2]
_EJEMPLO = _RAIZ / ".env.example"
_COMPOSE = _RAIZ / "docker-compose.yml"

# Las que el codigo exige y el compose no nombra, cada una con quien las exige.
EXIGIDAS_POR_CODIGO = {
    "TELEGRAM_WEBHOOK_SECRET": "fail-closed de panel/app/config.py en produccion",
    "TEST_ADMIN_PASSWORD": "panel/tests/conftest.py — sin ella ~400 tests dan 303",
}


def _declaradas() -> set[str]:
    return set(re.findall(r"(?m)^([A-Z][A-Z0-9_]*)=", _EJEMPLO.read_text(encoding="utf-8")))


def _pedidas_por_el_compose() -> set[str]:
    """Solo las lineas VIVAS: el servicio n8n esta comentado entero."""
    vivas = "\n".join(
        l for l in _COMPOSE.read_text(encoding="utf-8").splitlines()
        if not l.lstrip().startswith("#")
    )
    return set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)\}", vivas))


def test_el_compose_no_pide_nada_que_falte_en_el_ejemplo():
    faltan = _pedidas_por_el_compose() - _declaradas()
    assert not faltan, (
        f"`docker-compose.yml` interpola {sorted(faltan)} y `.env.example` no "
        "las lista. Compose inyecta la que falta como cadena VACIA, no como "
        "ausente: el default del codigo nunca se usa y no falla nada"
    )


@pytest.mark.parametrize("var,quien", sorted(EXIGIDAS_POR_CODIGO.items()))
def test_las_que_exige_el_codigo_estan_listadas(var, quien):
    assert var in _declaradas(), f"falta {var} — la exige {quien}"


def test_el_ejemplo_no_arrastra_variables_de_n8n():
    """N8N esta descomisionado (`dbce3cd`) y no se reinstala."""
    muertas = {v for v in _declaradas()
               if v.startswith(("DB_POSTGRESDB", "N8N_", "EXECUTIONS_"))
               or v in {"DB_TYPE", "WEBHOOK_URL", "GENERIC_TIMEZONE", "NODE_ENV"}}
    assert not muertas, f"volvieron variables de N8N a `.env.example`: {sorted(muertas)}"


def test_el_numero_de_whatsapp_lleva_su_prefijo():
    """Un `+595...` pelado no falla: pisa el default correcto y Twilio rechaza
    todo saliente en silencio. Es la trampa que el CLAUDE.md nombra."""
    m = re.search(r"(?m)^TWILIO_WHATSAPP_NUMBER=(.*)$", _EJEMPLO.read_text(encoding="utf-8"))
    assert m, "falta TWILIO_WHATSAPP_NUMBER"
    assert m.group(1).startswith("whatsapp:"), (
        f"el ejemplo dice `{m.group(1)}` y tiene que empezar con `whatsapp:`"
    )

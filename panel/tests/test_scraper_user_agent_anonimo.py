"""El User-Agent de los scrapers no debe nombrar al cliente.

Los cinco scrapers salen desde el mismo VPS que sirve el sitio público, así que
alguien que investigue va a poder atarlos igual. Lo que no corresponde es
anunciarlo en cada request: el header identificaba a la inmobiliaria por nombre
y con su dominio, en texto plano, a portales de la competencia.

El test mira el VALOR de las constantes, no el archivo, para no chocar con la
trampa de este repo (un test que prohíbe un patrón cuyo propio comentario lo
contiene).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_home_dir = str(Path(__file__).resolve().parent.parent.parent)
_scrapers_dir = str(Path(__file__).resolve().parent.parent.parent / "scrapers")
for _p in (_home_dir, _scrapers_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Cada token que ataría el request al cliente sin necesidad de investigar nada.
TOKENS_QUE_DELATAN = ("onnix", "onnix", "onnix sa", "onnix", "onnix")


def _assert_anonimo(valor: str, donde: str) -> None:
    bajo = valor.lower()
    encontrados = [t for t in TOKENS_QUE_DELATAN if t in bajo]
    assert not encontrados, (
        f"{donde} nombra al cliente en texto plano: {encontrados} en {valor!r}"
    )


def test_user_agent_compartido_de_los_scrapers_es_anonimo():
    from scrapers.shared.config import USER_AGENT

    _assert_anonimo(USER_AGENT, "scrapers.shared.config.USER_AGENT")


def test_user_agent_del_verificador_de_publicaciones_es_anonimo():
    from app.bot.scheduler.tasks.verification_scraper import _BROWSER_HEADERS

    _assert_anonimo(
        _BROWSER_HEADERS["User-Agent"], "verification_scraper._BROWSER_HEADERS"
    )


def test_user_agent_de_coldwell_es_anonimo():
    coldwell = pytest.importorskip(
        "scrapers.coldwell.scraper",
        reason="scrapers.coldwell.scraper no se pudo importar en este checkout",
    )
    _assert_anonimo(
        coldwell.BROWSER_HEADERS["User-Agent"], "coldwell.scraper.BROWSER_HEADERS"
    )

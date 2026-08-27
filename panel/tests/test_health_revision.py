"""/health expone la revisión con la que se buildeó la imagen.

Sin esto un deploy que no reemplazó nada se ve idéntico a uno que sí: el
contenedor levanta, /health dice ok, y sirve el código viejo. El pipeline
compara este campo contra el SHA que acaba de desplegar y contra el label
`org.opencontainers.image.revision` del contenedor.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.mark.asyncio
async def test_health_devuelve_la_revision_del_entorno(client, monkeypatch):
    monkeypatch.setenv("APP_REVISION", "0123456789abcdef0123456789abcdef01234567")
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["revision"] == "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.asyncio
async def test_health_sin_revision_devuelve_cadena_vacia(client, monkeypatch):
    monkeypatch.delenv("APP_REVISION", raising=False)
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["revision"] == ""

"""El flag `Secure` de las cookies sale de una variable, no de un `if pytest`.

Historia: `SessionMiddleware(https_only=...)` y el `Set-Cookie` del CSRF de
`main.py` decidian `Secure` con `os.getenv("PYTEST_CURRENT_TEST") is None`. Eso
deja exactamente un entorno sin cubrir: http fuera de pytest, o sea la laptop.
Safari y Firefox descartan una cookie `Secure` servida por http —tambien en
localhost, donde Chrome si la acepta, que es por que el bug se veia en un
navegador y no en otro—. Sin la cookie `csrf_token` el doble-submit no tiene
con que comparar y todo POST muere en 403: "La sesion expiro o el formulario
no es valido".

Los dos tests de abajo miran la cabecera `Set-Cookie` de verdad, no la
propiedad: si alguien vuelve a cablear `Secure` en `main.py` la propiedad
seguiria bien y el panel seguiria roto.
"""
from __future__ import annotations

import os

import pytest

from app.config import Settings


class TestFlagDesdeElEntorno:
    """`COOKIE_SECURE` manda, y el default es seguro."""

    def test_default_es_secure(self, monkeypatch):
        monkeypatch.delenv("COOKIE_SECURE", raising=False)
        assert Settings().cookie_secure is True

    @pytest.mark.parametrize("valor", ["false", "0", "no", "FALSE", ""])
    def test_valores_que_apagan(self, monkeypatch, valor):
        monkeypatch.setenv("COOKIE_SECURE", valor)
        assert Settings().cookie_secure is False

    @pytest.mark.parametrize("valor", ["true", "1", "yes", "TRUE"])
    def test_valores_que_prenden(self, monkeypatch, valor):
        monkeypatch.setenv("COOKIE_SECURE", valor)
        assert Settings().cookie_secure is True

    def test_pytest_no_es_una_rama(self, monkeypatch):
        """Correr bajo pytest no puede, por si solo, apagar el flag.

        Si vuelve el `and os.environ.get("PYTEST_CURRENT_TEST") is None`, esto
        se cae: estamos adentro de pytest y el flag tiene que seguir en True.
        """
        assert os.environ.get("PYTEST_CURRENT_TEST")
        monkeypatch.setenv("COOKIE_SECURE", "true")
        assert Settings().cookie_secure is True


class TestLaCabeceraQueLlegaAlNavegador:
    """Lo que importa es el `Set-Cookie`, no la propiedad."""

    def _cookies_emitidas(self, response) -> list[str]:
        # httpx expone las cabeceras repetidas via get_list.
        return response.headers.get_list("set-cookie")

    async def test_csrf_no_va_secure_sobre_http(self, client):
        """La suite corre con COOKIE_SECURE=false (ver conftest).

        Un GET sin cookie previa hace que el middleware emita una nueva, y esa
        cabecera no puede traer `Secure` sobre http.
        """
        client.cookies.clear()
        r = await client.get("/login")
        csrf = [c for c in self._cookies_emitidas(r) if c.startswith("csrf_token=")]
        assert csrf, "el middleware no emitio la cookie csrf_token"
        assert "Secure" not in csrf[0], csrf[0]

    async def test_la_cookie_csrf_sigue_siendo_legible_por_js(self, client):
        """El doble-submit necesita leerla desde JS: nunca HttpOnly."""
        client.cookies.clear()
        r = await client.get("/login")
        csrf = [c for c in self._cookies_emitidas(r) if c.startswith("csrf_token=")]
        assert csrf
        assert "HttpOnly" not in csrf[0], csrf[0]

    async def test_el_login_pasa_el_gate_de_csrf(self, client):
        """Regresion directa del sintoma: POST /login con el token de la cookie
        NO puede volver 403. El email no existe a proposito — lo que se prueba
        es el gate, no el login: 401 esta bien, 403 no. Un email inexistente
        ademas no toca el lockout del admin real.
        """
        client.cookies.clear()
        await client.get("/login")
        token = client.cookies.get("csrf_token")
        assert token, "sin cookie csrf no hay nada que probar"
        r = await client.post(
            "/login",
            data={
                "email": "no-existe-cookie-secure@example.com",
                "password": "credencial-invalida-a-proposito",
                "csrf_token": token,
            },
        )
        assert r.status_code != 403, r.text[:200]

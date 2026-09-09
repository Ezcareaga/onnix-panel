"""El webhook unico de Meta: handshake, firma y despacho por `object`.

Tres cosas distintas y cada una con su forma de fallar:

1. **El handshake** tiene que comparar el token ANTES de devolver el challenge.
   Un endpoint que devuelve `hub.challenge` sin mirar `hub.verify_token` deja
   que cualquiera suscriba su app a esta URL.
2. **La firma** se calcula sobre el cuerpo CRUDO. Es la parte que mas facil se
   rompe sola: basta con que alguien parsee el JSON y lo vuelva a serializar
   antes de firmar para que una firma valida empiece a rechazarse.
3. **El despacho** por `object` es lo que hace que sea UN endpoint y no tres.

Y la que no se ve: `/webhooks/meta` es plural. La exencion de CSRF listaba
`/webhook/` en singular, asi que el POST moria en 403 antes de llegar a
verificar su firma. Eso tiene su test aparte, abajo del todo.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.bot.webhooks.meta import parsear_evento, verificar_firma
from app.utils.csrf import csrf_token_valid

_SECRET = "app-secret-de-pytest"
_VERIFY = "verify-token-de-pytest"


def _firmar(cuerpo: bytes, secret: str = _SECRET) -> str:
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), cuerpo, hashlib.sha256,
    ).hexdigest()


def _mensaje(object_: str = "instagram", texto: str = "Hola", mid: str = "mid_1") -> dict:
    return {
        "object": object_,
        "entry": [{
            "id": "17841400000000000",
            "time": 1757000000,
            "messaging": [{
                "sender": {"id": "USER_123"},
                "recipient": {"id": "PAGE_456"},
                "timestamp": 1757000000,
                "message": {"mid": mid, "text": texto},
            }],
        }],
    }


@pytest.fixture
def client():
    """App con el secreto y el verify token puestos, y el guardado mockeado."""
    with patch(
        "app.bot.webhooks.meta._get_app_secret", return_value=_SECRET,
    ), patch(
        "app.bot.webhooks.meta._get_verify_token", return_value=_VERIFY,
    ), patch(
        "app.bot.webhooks.meta._procesar", new_callable=AsyncMock,
    ):
        from app.main import app
        yield TestClient(app)


# ---------------------------------------------------------------------------
# 1. Handshake
# ---------------------------------------------------------------------------

class TestHandshake:
    def test_devuelve_el_challenge_crudo_con_el_token_correcto(self, client):
        resp = client.get("/webhooks/meta", params={
            "hub.mode": "subscribe",
            "hub.verify_token": _VERIFY,
            "hub.challenge": "1158201444",
        })
        assert resp.status_code == 200
        # Crudo: Meta espera el numero pelado, no JSON. `"1158201444"` con
        # comillas hace fallar la verificacion en el App Dashboard.
        assert resp.text == "1158201444"
        assert resp.headers["content-type"].startswith("text/plain")

    def test_token_incorrecto_no_devuelve_el_challenge(self, client):
        resp = client.get("/webhooks/meta", params={
            "hub.mode": "subscribe",
            "hub.verify_token": "token-de-otro",
            "hub.challenge": "1158201444",
        })
        assert resp.status_code == 403
        assert "1158201444" not in resp.text

    def test_modo_distinto_de_subscribe_se_rechaza(self, client):
        resp = client.get("/webhooks/meta", params={
            "hub.mode": "unsubscribe",
            "hub.verify_token": _VERIFY,
            "hub.challenge": "1158201444",
        })
        assert resp.status_code == 403

    def test_sin_verify_token_configurado_se_rechaza(self):
        """Sin token configurado no se puede comparar nada: 403, no 200."""
        with patch("app.bot.webhooks.meta._get_verify_token", return_value=""):
            from app.main import app
            c = TestClient(app)
            resp = c.get("/webhooks/meta", params={
                "hub.mode": "subscribe",
                "hub.verify_token": "",
                "hub.challenge": "123",
            })
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 2. Firma
# ---------------------------------------------------------------------------

class TestFirma:
    def test_firma_valida_pasa(self):
        cuerpo = b'{"object":"instagram"}'
        assert verificar_firma(cuerpo, _firmar(cuerpo), _SECRET) is True

    def test_firma_de_otro_secreto_no_pasa(self):
        cuerpo = b'{"object":"instagram"}'
        assert verificar_firma(cuerpo, _firmar(cuerpo, "otro-secreto"), _SECRET) is False

    def test_un_byte_distinto_en_el_cuerpo_invalida(self):
        cuerpo = b'{"object":"instagram"}'
        firma = _firmar(cuerpo)
        assert verificar_firma(cuerpo + b" ", firma, _SECRET) is False

    @pytest.mark.parametrize("cabecera", ["", "abc123", "sha1=abc", "sha256="])
    def test_cabecera_malformada_no_pasa(self, cabecera):
        assert verificar_firma(b"{}", cabecera, _SECRET) is False

    def test_el_post_rechaza_una_firma_invalida(self, client):
        cuerpo = json.dumps(_mensaje()).encode()
        resp = client.post(
            "/webhooks/meta",
            content=cuerpo,
            headers={
                "X-Hub-Signature-256": _firmar(cuerpo, "secreto-equivocado"),
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 403

    def test_el_post_rechaza_si_falta_la_firma(self, client):
        cuerpo = json.dumps(_mensaje()).encode()
        resp = client.post(
            "/webhooks/meta",
            content=cuerpo,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 403

    def test_la_firma_se_valida_sobre_el_cuerpo_crudo(self, client):
        """El JSON se manda con espacios raros a proposito.

        Si alguien firmara el JSON re-serializado en vez de los bytes que
        llegaron, este cuerpo —valido y bien firmado— empezaria a dar 403.
        """
        cuerpo = b'{"object":  "instagram",\n  "entry": []}'
        resp = client.post(
            "/webhooks/meta",
            content=cuerpo,
            headers={
                "X-Hub-Signature-256": _firmar(cuerpo),
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 3. Despacho por `object`
# ---------------------------------------------------------------------------

class TestDespacho:
    @pytest.mark.parametrize("object_,canal", [
        ("instagram", "instagram"),
        ("page", "messenger"),
    ])
    def test_el_object_decide_el_canal(self, object_, canal):
        [req] = parsear_evento(_mensaje(object_=object_))
        assert req.platform == canal

    def test_whatsapp_todavia_no_entra_por_aca(self):
        """WhatsApp sigue por Twilio. Cuando migre, se agrega al despacho."""
        assert parsear_evento(_mensaje(object_="whatsapp_business_account")) == []

    def test_un_object_desconocido_no_explota(self):
        assert parsear_evento(_mensaje(object_="user")) == []

    def test_el_id_que_se_guarda_es_el_del_usuario_no_el_de_la_pagina(self):
        """Es el unico dato con el que se vuelve a encontrar a la persona.

        Guardar el `recipient.id` —que es la pagina— haria que TODOS los
        mensajes de un canal caigan en el mismo contacto.
        """
        [req] = parsear_evento(_mensaje())
        assert req.user_id == "USER_123"
        assert req.chat_id == "USER_123"
        assert "PAGE_456" not in (req.user_id, req.chat_id)

    def test_el_lote_entero_se_procesa_no_solo_el_primero(self):
        """Meta agrupa: `entry` es una lista y cada una trae varios eventos."""
        cuerpo = {
            "object": "instagram",
            "entry": [
                {"messaging": [
                    {"sender": {"id": "U1"}, "message": {"mid": "m1", "text": "uno"}},
                    {"sender": {"id": "U2"}, "message": {"mid": "m2", "text": "dos"}},
                ]},
                {"messaging": [
                    {"sender": {"id": "U3"}, "message": {"mid": "m3", "text": "tres"}},
                ]},
            ],
        }
        assert [r.text for r in parsear_evento(cuerpo)] == ["uno", "dos", "tres"]

    def test_el_eco_de_lo_que_mandamos_nosotros_se_ignora(self):
        """Sin esto, cada mensaje saliente vuelve a entrar como si fuera del
        cliente y el hilo se duplica solo."""
        cuerpo = _mensaje()
        cuerpo["entry"][0]["messaging"][0]["message"]["is_echo"] = True
        assert parsear_evento(cuerpo) == []

    @pytest.mark.parametrize("evento", [
        {"sender": {"id": "U1"}, "delivery": {"mids": ["m1"]}},
        {"sender": {"id": "U1"}, "read": {"watermark": 1757000000}},
        {"sender": {"id": "U1"}, "reaction": {"emoji": "❤"}},
    ])
    def test_los_eventos_que_no_son_mensajes_se_ignoran(self, evento):
        cuerpo = {"object": "instagram", "entry": [{"messaging": [evento]}]}
        assert parsear_evento(cuerpo) == []

    @pytest.mark.parametrize("mensaje", [
        {"mid": "m1"},
        {"mid": "m1", "text": ""},
        {"mid": "m1", "text": "   "},
        {"mid": "m1", "attachments": [{"type": "image"}]},
    ])
    def test_un_mensaje_sin_texto_no_se_guarda(self, mensaje):
        cuerpo = {
            "object": "instagram",
            "entry": [{"messaging": [{"sender": {"id": "U1"}, "message": mensaje}]}],
        }
        assert parsear_evento(cuerpo) == []

    def test_el_mid_queda_como_external_id(self):
        """Es lo que usa el middleware de idempotencia para no guardar dos
        veces el mismo mensaje cuando Meta reintenta el lote."""
        [req] = parsear_evento(_mensaje(mid="mid_abc123"))
        assert req.external_id == "mid_abc123"

    def test_un_entry_que_no_es_dict_no_rompe_el_lote(self):
        cuerpo = {"object": "instagram", "entry": ["basura", None]}
        assert parsear_evento(cuerpo) == []


# ---------------------------------------------------------------------------
# 4. Que el 200 sea 200
# ---------------------------------------------------------------------------

class TestSiempreContesta200:
    """Meta reintenta el LOTE ENTERO cuando el endpoint no contesta 200.

    Un evento que no sabemos manejar tiene que contestar 200 igual o entra en
    loop de reintentos. Lo unico que NO puede contestar 200 es una firma
    invalida, que tiene su test arriba.
    """

    @pytest.mark.parametrize("cuerpo", [
        b"{}",
        b'{"object": "user", "entry": []}',
        b'{"object": "instagram", "entry": []}',
        b'{"object": "instagram", "entry": [{"messaging": []}]}',
        b"[]",
        b"no soy json",
    ])
    def test_un_cuerpo_raro_contesta_200(self, client, cuerpo):
        resp = client.post(
            "/webhooks/meta",
            content=cuerpo,
            headers={
                "X-Hub-Signature-256": _firmar(cuerpo),
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200

    def test_un_mensaje_valido_encola_su_procesamiento(self, client):
        cuerpo = json.dumps(_mensaje()).encode()
        with patch(
            "app.bot.webhooks.meta._procesar", new_callable=AsyncMock,
        ) as mock_proc:
            resp = client.post(
                "/webhooks/meta",
                content=cuerpo,
                headers={
                    "X-Hub-Signature-256": _firmar(cuerpo),
                    "Content-Type": "application/json",
                },
            )
        assert resp.status_code == 200
        mock_proc.assert_awaited_once()
        assert mock_proc.await_args[0][0].text == "Hola"


# ---------------------------------------------------------------------------
# 5. La exencion de CSRF, que es plural
# ---------------------------------------------------------------------------

class TestExencionDeCsrf:
    def test_la_ruta_de_meta_esta_exenta(self):
        """Regresion directa: con la exencion en `/webhook/` singular, el POST
        de Meta moria en 403 de CSRF ANTES de llegar a verificar su firma."""
        assert csrf_token_valid(
            "POST", "/webhooks/meta", cookie_token=None,
            header_token=None, form_token=None,
        ) is True

    def test_las_rutas_de_twilio_siguen_exentas(self):
        for ruta in ("/webhook/whatsapp", "/webhook/whatsapp/status"):
            assert csrf_token_valid(
                "POST", ruta, cookie_token=None,
                header_token=None, form_token=None,
            ) is True, ruta

    @pytest.mark.parametrize("ruta", [
        "/webhookcualquiera",
        "/webhooks",
        "/login",
        "/settings",
    ])
    def test_la_exencion_no_se_derrama(self, ruta):
        """Un `startswith('/webhook')` pelado eximiria `/webhookcualquiera`."""
        assert csrf_token_valid(
            "POST", ruta, cookie_token=None,
            header_token=None, form_token=None,
        ) is False, ruta

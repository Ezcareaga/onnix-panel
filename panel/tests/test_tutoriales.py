"""La página de tutoriales y su única fuente de verdad.

Los cinco videos existen en dos lados: `GUIONES` en
`tutoriales/video/src/tutorial/guion.ts`, que es lo que Remotion renderiza, y
`TUTORIALES` en `app/routes/tutoriales.py`, que es lo que el panel muestra. La
lista de acá compara las dos y se pone roja si alguna se mueve sin la otra —
que es exactamente cómo se pierde un tutorial nuevo: se renderiza el MP4 y el
panel nunca lo lista.
"""
import logging
import re
from pathlib import Path

import pytest

from app.routes.tutoriales import _POR_ARCHIVO, SOLO_ADMIN, TUTORIALES, slug_de

# panel/tests/ -> panel/ -> raíz del repo
_GUION_TS = Path(__file__).resolve().parents[2] / "tutoriales/video/src/tutorial/guion.ts"


def _guiones_del_ts() -> list[tuple[str, str, str]]:
    """(id, titulo, promesa) de cada entrada de GUIONES, en orden.

    Se parsea el bloque de `GUIONES` y no el archivo entero: el tipo `Guion`
    declara los mismos tres nombres de campo arriba, y contarlos daría una
    entrada fantasma. Es la misma trampa que el repo ya tiene escrita para los
    comentarios que contienen lo que el test prohíbe.
    """
    fuente = _GUION_TS.read_text(encoding="utf-8")
    inicio = fuente.index("export const GUIONES")
    cuerpo = fuente[inicio:]

    entradas: list[tuple[str, str, str]] = []
    for bloque in re.finditer(
        r'id:\s*"(?P<id>[^"]+)",\s*'
        r'titulo:\s*"(?P<titulo>[^"]+)",\s*'
        r'promesa:\s*\n?\s*"(?P<promesa>[^"]+)",',
        cuerpo,
    ):
        entradas.append(
            (bloque.group("id"), bloque.group("titulo"), bloque.group("promesa"))
        )
    return entradas


def test_el_ts_se_parsea():
    """Prueba negativa del parser: si deja de encontrar guiones, el resto miente.

    Sin esto, un cambio de formato en `guion.ts` dejaría a `_guiones_del_ts()`
    devolviendo `[]`, y una lista vacía es igual a cualquier otra lista vacía:
    los tests de abajo se pondrían verdes sin comparar nada.
    """
    guiones = _guiones_del_ts()
    assert len(guiones) >= 5, f"el parser encontró {len(guiones)} guiones en {_GUION_TS}"


def test_el_panel_lista_los_mismos_tutoriales_que_el_guion():
    assert TUTORIALES == _guiones_del_ts(), (
        "app/routes/tutoriales.py y tutoriales/video/src/tutorial/guion.ts "
        "dejaron de decir lo mismo"
    )


@pytest.mark.parametrize(
    "id_composicion, esperado",
    [
        ("ContestarUnaConversacion", "contestar-una-conversacion"),
        ("AtenderLosLeads", "atender-los-leads"),
        ("LaFichaDelCliente", "la-ficha-del-cliente"),
    ],
)
def test_slug_de_convierte_el_id_a_nombre_de_archivo(id_composicion, esperado):
    """El nombre del MP4 sale del id de la composición, no de una segunda lista.

    `scripts/render_tutoriales.sh` aplica la misma transformación al renderizar.
    Si esto cambia, el panel apunta a archivos que no existen.
    """
    assert slug_de(id_composicion) == esperado


@pytest.mark.asyncio
async def test_tutoriales_pide_sesion(client):
    respuesta = await client.get("/tutoriales", follow_redirects=False)
    assert respuesta.status_code == 303


@pytest.mark.asyncio
async def test_el_asesor_ve_los_tutoriales(agent_client):
    """No es admin-only a propósito: el asesor es quien tiene que aprender."""
    respuesta = await agent_client.get("/tutoriales")
    assert respuesta.status_code == 200


def test_el_de_usuarios_es_solo_para_admin():
    """El conjunto no está vacío: si lo estuviera, los tests de abajo no probarían nada.

    Nombra el id a mano, y ese es el punto: un test que se parametrice sobre
    SOLO_ADMIN no puede ver que alguien lo vació.
    """
    assert "LosUsuariosDelPanel" in SOLO_ADMIN


@pytest.mark.asyncio
async def test_el_asesor_no_ve_la_tarjeta_del_video_de_usuarios(agent_client):
    html = (await agent_client.get("/tutoriales")).text
    assert "Los usuarios del panel" not in html
    assert "/videos/los-usuarios-del-panel.mp4" not in html


@pytest.mark.asyncio
async def test_el_admin_si_ve_la_tarjeta_del_video_de_usuarios(admin_client):
    """La otra mitad del par: sin esto, esconderlo de TODOS daría verde igual."""
    html = (await admin_client.get("/tutoriales")).text
    assert "Los usuarios del panel" in html
    assert '<source src="/videos/los-usuarios-del-panel.mp4" type="video/mp4">' in html


@pytest.mark.asyncio
async def test_el_archivo_restringido_no_se_baja_con_la_url(agent_client):
    """Esconder la tarjeta y dejar el archivo abierto es una puerta con cortina.

    El asesor tiene sesión válida: lo que se prueba es el rol, no el login.
    """
    respuesta = await agent_client.get("/videos/los-usuarios-del-panel.mp4")
    assert respuesta.status_code == 403


@pytest.mark.asyncio
async def test_los_videos_piden_sesion(client):
    """Sin sesión no hay MP4. Un mount de StaticFiles daba 200 acá."""
    respuesta = await client.get(
        "/videos/contestar-una-conversacion.mp4", follow_redirects=False
    )
    assert respuesta.status_code == 303


def test_el_indice_de_archivos_tiene_exactamente_los_cinco():
    """Lo que resuelve un nombre a un permiso, sin pasar por HTTP.

    Es pura lógica y por eso no depende de que el directorio de videos exista.
    La versión por HTTP no alcanza sola: en la máquina de tests `/app/tutoriales`
    NO existe, así que TODO da 404 y un 404 no distingue «este nombre no es un
    tutorial» de «el archivo no está en el disco».
    """
    esperados = {f"{slug_de(id_c)}.mp4" for id_c, _t, _p in TUTORIALES}
    assert set(_POR_ARCHIVO) == esperados
    assert len(esperados) == 5
    for intento in ("../../etc/passwd", "..", "", "contestar-una-conversacion.mp4.bak"):
        assert intento not in _POR_ARCHIVO


@pytest.mark.asyncio
async def test_un_nombre_desconocido_se_rechaza_sin_mirar_el_disco(admin_client, caplog):
    """404 por no estar en el índice, no por faltar el archivo.

    Los dos caminos devuelven 404 y el status code solo no los separa — con el
    directorio ausente el test daba verde aunque el nombre se concatenara al
    path. Lo que los separa es el log: sólo el camino que mira el disco escribe
    «Falta el MP4».
    """
    with caplog.at_level(logging.ERROR, logger="app.routes.tutoriales"):
        respuesta = await admin_client.get("/videos/no-existe.mp4")
    assert respuesta.status_code == 404
    assert "Falta el MP4" not in caplog.text


@pytest.mark.asyncio
async def test_cada_tutorial_tiene_su_video(admin_client):
    respuesta = await admin_client.get("/tutoriales")
    assert respuesta.status_code == 200
    html = respuesta.text

    for id_composicion, titulo, promesa in TUTORIALES:
        archivo = f"/videos/{slug_de(id_composicion)}.mp4"
        # El `<source>` exacto, no la URL suelta: el <video> lleva adentro un
        # link de descarga a la MISMA URL, así que `archivo in html` seguía
        # verde con el <source> borrado y el video sin reproducir. Es el assert
        # por substring de CLAUDE.md, encontrado con la mutación de sanidad.
        assert f'<source src="{archivo}" type="video/mp4">' in html, (
            f"falta el <source> reproducible de {id_composicion}"
        )
        assert titulo in html, f"falta el título de {id_composicion}"
        assert promesa in html, f"falta la promesa de {id_composicion}"


@pytest.mark.asyncio
async def test_el_menu_lleva_a_los_tutoriales(admin_client):
    respuesta = await admin_client.get("/tutoriales")
    assert 'href="/tutoriales"' in respuesta.text
    # El item activo se marca con aria-current, no sólo con color.
    assert 'aria-current="page"' in respuesta.text


@pytest.mark.asyncio
async def test_ningun_video_sale_de_un_tercero(admin_client):
    """El CSP del panel es `default-src 'self'` y `media-src` cae en ese default.

    Un embed de YouTube o de OneDrive obligaría a abrirle `frame-src` a un
    tercero para servir 136 MB que ya están en el servidor. Este test es el que
    se pone rojo si alguien lo intenta.
    """
    html = (await admin_client.get("/tutoriales")).text
    for tercero in ("youtube.com", "youtu.be", "onedrive", "sharepoint", "vimeo"):
        assert tercero not in html.lower(), f"{tercero} en la página de tutoriales"

"""Tutoriales en video — GET /tutoriales.

Los MP4 NO viven en el repo ni en la imagen Docker. Se renderizan con Remotion
(`tutoriales/video/`) y se copian a `/home/onnix/tutoriales/` en el VPS, que
`docker-compose.yml` monta `:ro` en `/app/tutoriales`. Es el mismo patrón que
las fotos de propiedades (`/home/onnix/images`): 136 MB de video son estado
del servidor, no código, y meterlos en la imagen la engorda en cada rebuild del
pipeline.

**Los archivos los sirve `GET /videos/{nombre}` de este módulo, NO un mount de
StaticFiles.** Un mount queda fuera de `get_current_user`: `/tutoriales` pedía
sesión y el MP4 lo bajaba cualquiera con la URL. Con un tutorial restringido a
admin eso deja de ser un detalle — esconder la tarjeta y dejar el archivo
abierto es la puerta con cortina que `sidebar.html` ya nombra para los links de
admin. `FileResponse` de Starlette resuelve `Range` solo, así que el seek dentro
del video se conserva.

El nombre del archivo se deriva del `id` de la composición de Remotion en
kebab-case: `ContestarUnaConversacion` -> `contestar-una-conversacion.mp4`. Eso
es lo que hace `scripts/render_tutoriales.sh`, y no hay una segunda lista de
nombres en ningún lado.

`TUTORIALES` repite el id, el título y la promesa que ya viven en
`tutoriales/video/src/tutorial/guion.ts`. Está repetido a propósito —el panel no
parsea TypeScript en runtime— y `test_tutoriales.py` compara las dos listas y se
pone rojo si alguna se mueve sin la otra.
"""
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from app.dependencies import get_current_user
from app.models.user import User
from app.tz import get_templates

logger = logging.getLogger(__name__)

router = APIRouter()
templates = get_templates()

# Donde docker-compose monta `/home/onnix/tutoriales` (`:ro`).
DIRECTORIO_VIDEOS = "/app/tutoriales"


def slug_de(id_composicion: str) -> str:
    """`ContestarUnaConversacion` -> `contestar-una-conversacion`.

    La misma transformación que aplica `scripts/render_tutoriales.sh` al nombrar
    el MP4. Escrita una vez acá y usada por el test que compara contra el guion.
    """
    partes: list[str] = []
    actual = ""
    for caracter in id_composicion:
        if caracter.isupper() and actual:
            partes.append(actual)
            actual = caracter.lower()
        else:
            actual += caracter.lower()
    if actual:
        partes.append(actual)
    return "-".join(partes)


# (id de la composición, título, promesa) — espejo de GUIONES en guion.ts.
TUTORIALES: list[tuple[str, str, str]] = [
    (
        "ContestarUnaConversacion",
        "Contestar una conversación",
        "Cómo ver lo que te escribió un cliente por WhatsApp y contestarle desde el panel.",
    ),
    (
        "AtenderLosLeads",
        "Atender los leads del día",
        "De dónde salen los leads, cada cuánto entran, y cómo pasarle uno a un asesor.",
    ),
    (
        "PasarUnaPropiedad",
        "Pasarle una propiedad a un cliente",
        "Cómo encontrar una propiedad en el stock y mandarle el link al cliente.",
    ),
    (
        "LaFichaDelCliente",
        "La ficha del cliente",
        "Cómo encontrar a un cliente, dejarle notas y ponerte un recordatorio para no perderlo.",
    ),
    (
        "LosUsuariosDelPanel",
        "Los usuarios del panel",
        "Cómo darle acceso a alguien del equipo, qué puede ver cada rol, y cómo cambiar una contraseña.",
    ),
]


# Los tutoriales que sólo ve un administrador.
#
# «Los usuarios del panel» enseña a dar y sacar accesos y a cambiar contraseñas
# — su propio guion dice «sólo la ve un administrador» sobre la pestaña que
# muestra, y `/settings` pide `require_admin`. Un tutorial de una pantalla que
# el asesor no puede abrir, además de no servirle, le enseña el mapa de los
# permisos.
#
# Es un conjunto aparte y no un cuarto campo de TUTORIALES a propósito: esa
# lista es el espejo exacto de `GUIONES` en guion.ts y el test las compara tupla
# a tupla. Quién puede ver un video es una decisión del panel, no del guion.
SOLO_ADMIN: frozenset[str] = frozenset({"LosUsuariosDelPanel"})


def _puede_ver(user: User, id_composicion: str) -> bool:
    return id_composicion not in SOLO_ADMIN or user.role == "admin"


# El nombre de archivo -> id de composición, para resolver la URL a un permiso.
# Se deriva de TUTORIALES, así que no hay una segunda lista que se desincronice:
# un tutorial que no está en TUTORIALES no se sirve, y ese es el filtro que
# convierte `{nombre}` en algo que no puede salir del directorio.
_POR_ARCHIVO: dict[str, str] = {
    f"{slug_de(id_composicion)}.mp4": id_composicion
    for id_composicion, _titulo, _promesa in TUTORIALES
}


@router.get("/tutoriales", response_class=HTMLResponse)
async def tutoriales_page(request: Request, user: User = Depends(get_current_user)):
    """La lista de tutoriales. La ve cualquiera que entre al panel.

    No pide admin a propósito: el asesor es justamente quien necesita aprender a
    contestar una conversación y a pasar una propiedad. Lo que sí filtra es qué
    tarjetas se listan, con el mismo predicado que decide si el archivo se sirve.
    """
    logger.info("Tutoriales accessed: user=%s", user.email)
    videos = [
        {
            "id": id_composicion,
            "titulo": titulo,
            "promesa": promesa,
            "archivo": f"/videos/{slug_de(id_composicion)}.mp4",
        }
        for id_composicion, titulo, promesa in TUTORIALES
        if _puede_ver(user, id_composicion)
    ]
    return templates.TemplateResponse(
        "tutoriales.html", {"request": request, "user": user, "videos": videos}
    )


@router.get("/videos/{nombre}")
async def video(nombre: str, user: User = Depends(get_current_user)):
    """Sirve un MP4 de tutorial, con sesión y con el rol que ese tutorial pide.

    El orden importa: `get_current_user` corre primero (sin sesión, 303 al
    login), después el permiso, y recién al final se mira el disco. Así un
    asesor recibe el mismo 403 exista o no el archivo, y el 404 no le sirve de
    oráculo para saber qué tutoriales hay.

    `nombre` NO se concatena a un path: se busca en `_POR_ARCHIVO`, que se
    deriva de TUTORIALES. Cualquier cosa que no sea uno de esos nombres exactos
    —incluido un `../`— no está en el diccionario y sale por el 404.
    """
    id_composicion = _POR_ARCHIVO.get(nombre)
    if id_composicion is None:
        raise HTTPException(status_code=404)

    if not _puede_ver(user, id_composicion):
        logger.warning(
            "Tutorial restringido negado: user=%s role=%s video=%s",
            user.email, user.role, nombre,
        )
        raise HTTPException(status_code=403)

    ruta = os.path.join(DIRECTORIO_VIDEOS, nombre)
    if not os.path.isfile(ruta):
        # Los MP4 se copian a mano al VPS (ver el docstring de arriba): el
        # archivo puede faltar sin que falte el código. Se loguea porque desde
        # el navegador esto es un reproductor mudo y nada más.
        logger.error("Falta el MP4 del tutorial en el disco: %s", ruta)
        raise HTTPException(status_code=404)

    return FileResponse(ruta, media_type="video/mp4")

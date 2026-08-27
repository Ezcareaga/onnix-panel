"""La URL pública de una foto de propiedad.

**El portal de origen no puede leerse en la URL.** La ficha pública se presenta
como una publicación de Onnix SA: título, precio, fotos y un botón de
WhatsApp, sin una sola mención al portal del que salió el aviso. Pero las fotos
se servían desde ``/images/remax/143028064-85/1.webp`` — el nombre estaba en el
HTML, en la URL de cada imagen y en el menú contextual del navegador.

El disco sigue organizado por fuente (``/home/onnix/images/<fuente>/``), que
es lo que escriben los scrapers y lo que respalda el backup. Lo que cambia es la
URL: nginx traduce el alias al directorio real, y ningún alias dice de dónde
salió el aviso.

**Un solo armador.** El path estaba escrito seis veces —dos repositorios, dos
servicios, dos plantillas— más el generador de la landing. Es el patrón que en
este repo ya divergió cuatro veces: el día que hay que cambiar la URL hay que
acordarse de las seis.
"""

from __future__ import annotations

# Alias por fuente. Neutro a propósito: `p` de portal y un número, sin ninguna
# relación con el nombre ni con el orden alfabético de las fuentes.
#
# NUNCA reordenar ni reasignar: cada alias es una URL pública que ya está en el
# HTML de la landing, en el índice de imágenes de Google y en cualquier link que
# alguien haya compartido. Sumar una fuente = sumar un alias nuevo, y su
# `location` en nginx.
ALIAS_POR_FUENTE: dict[str, str] = {
    "onnixpy": "p3",
    "remax": "p1",
    "coldwell": "p4",
    "psir": "p2",
}


def alias_de_fuente(source: str | None) -> str | None:
    """El alias público de una fuente, o None si no tiene."""
    if not source:
        return None
    return ALIAS_POR_FUENTE.get(source)


def url_foto(source: str | None, external_id: str | None, n: int = 1) -> str | None:
    """La URL pública de la foto `n` de una propiedad.

    Devuelve None cuando la fuente no tiene alias —una fuente nueva sin su
    `location` en nginx daría 404, y un None deja que el llamador muestre el
    placeholder en vez de una imagen rota.
    """
    alias = alias_de_fuente(source)
    if not alias or not external_id:
        return None
    return f"/images/{alias}/{external_id}/{n}.webp"


def urls_fotos(source: str | None, external_id: str | None, cantidad: int) -> list[str]:
    """Las `cantidad` fotos de una propiedad, en orden."""
    if not cantidad or cantidad < 1:
        return []
    urls = [url_foto(source, external_id, i) for i in range(1, cantidad + 1)]
    return [u for u in urls if u]

"""Las propiedades sin foto van al final del listado, no al principio.

Pedido de Ez el 2026-08-24: «al menos que no aparezcan primerito en las
propiedades». **No se ocultan** — la ficha publica tiene un estado sin-foto
hecho a proposito (`galeria-sin-fotos`) que pide las fotos por WhatsApp y
captura el lead igual. Lo que cambia es el orden.

Son 89 de 19.941 activas, medidas en produccion el 2026-08-24: 88 tienen la URL
del CDN con el token vencido —dan 403— y una nunca tuvo URL.

**El panel y el portal publico comparten `list_with_filters`**, asi que el orden
se decide una sola vez. Este archivo cuida que se siga decidiendo una sola vez:
`list_ids_with_filters` es la pata SQL de la fusion RRF y su docstring exige
«identical ORDER BY». Dos funciones que tienen que ordenar igual y lo escriben
por separado es como empieza una divergencia.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.repositories.property_repo import _orden_listado

_FUENTE = Path(__file__).resolve().parents[1] / "app" / "repositories" / "property_repo.py"


def _sin_comentarios(texto: str) -> str:
    """Sin comentarios `#` ni docstrings.

    El comentario que explica el patron lo contiene: arriba de `_orden_listado`
    hay un bloque que nombra `local_image_count` y `ORDER BY` enteros.
    """
    texto = re.sub(r'"""(?:.|\n)*?"""', "", texto)
    return re.sub(r"(?m)#.*$", "", texto)


def _claves(orden: str) -> list[str]:
    """Los criterios del ORDER BY, separando por comas de PRIMER nivel.

    Un `split(",")` pelado corta adentro de `COALESCE(local_image_count, 0)` y
    deja `"(COALESCE(local_image_count"` como primera clave. Lo aprendi con el
    test en rojo sobre codigo correcto — que es el otro modo de mentir: el rojo
    que no habla del codigo.
    """
    claves, prof, actual = [], 0, ""
    for ch in orden:
        if ch == "(":
            prof += 1
        elif ch == ")":
            prof -= 1
        if ch == "," and prof == 0:
            claves.append(actual.strip())
            actual = ""
        else:
            actual += ch
    if actual.strip():
        claves.append(actual.strip())
    return claves


def test_lo_que_no_tiene_foto_va_despues():
    """`false` ordena antes que `true`, asi que `(count = 0) ASC` pone primero
    a las que SI tienen foto."""
    orden = _orden_listado("active")
    assert "local_image_count" in orden, (
        f"el orden no mira las fotos: {orden!r}"
    )
    clave = _claves(orden)[0]
    assert "local_image_count" in clave, (
        f"las fotos no son el primer criterio de orden: {orden!r}. Si van "
        "segundas, la fecha manda y una sin foto vuelve a encabezar"
    )
    assert clave.strip().endswith("ASC"), (
        f"el criterio de fotos no es ASC: {clave!r}. Con DESC las sin foto "
        "pasan a ir PRIMERO, que es exactamente el bug al reves"
    )


def test_el_orden_por_fecha_no_se_perdio():
    """Meter las fotos adelante no puede pisar lo que ya ordenaba."""
    assert "created_at DESC" in _orden_listado("active")
    assert "created_at DESC" in _orden_listado(None)
    assert "updated_at DESC" in _orden_listado("inactive")


def test_el_orden_se_escribe_una_sola_vez():
    """Las dos funciones que listan tienen que llamar al mismo helper.

    No se compara el string: se cuenta que ninguna arme su propio ORDER BY.
    """
    fuente = _sin_comentarios(_FUENTE.read_text(encoding="utf-8"))
    propios = re.findall(r'order\s*=\s*(?!_orden_listado)\S', fuente)
    assert not propios, (
        "alguna funcion arma su propio `order` en vez de llamar a "
        "`_orden_listado`. `list_ids_with_filters` es la pata SQL de la fusion "
        "RRF: si ordena distinto que `list_with_filters`, la fusion mezcla dos "
        "listas que no son la misma"
    )
    assert fuente.count("_orden_listado(") >= 3, (
        "se esperaban al menos la definicion y los dos llamadores"
    )

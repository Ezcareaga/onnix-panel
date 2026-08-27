"""El mínimo de la contraseña se declara en cuatro lugares y decía dos cosas.

`users.py` exige **12 caracteres** en las dos rutas que tocan contraseñas
(`:71` al crear y `:205` al cambiar). Tres formularios del panel declaraban
`minlength="12"` y el cuarto —`partials/user_edit_row.html:54`— decía **8**,
con un placeholder que además prometía «mín. 8 caracteres».

El resultado no era un error visible, que sería lo de menos: con una contraseña
de entre 8 y 11 el navegador la dejaba mandar, el servidor levantaba un
`HTTPException(400)`, y **HTMX no hace swap en una respuesta 4xx**. la administradora
apretaba «Cambiar contraseña» y no pasaba absolutamente nada — ni el cambio, ni
un mensaje, ni una pista de qué había estado mal.

Es la forma que este repo ya conoce: **lo escrito dos veces ya divergió**, sólo
que acá estaba escrito cuatro veces.

Este archivo ata el número del servidor al de los formularios, en vez de repetir
el 12 en un quinto lugar: `MINIMO` se **importa** de donde vive la regla.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PANEL = Path(__file__).resolve().parent.parent
_TEMPLATES = _PANEL / "app" / "templates"
_RUTAS = _PANEL / "app" / "routes" / "users.py"


def _sin_comentarios(texto: str) -> str:
    """Sin comentarios de Jinja ni de HTML.

    La trampa propia de este repo: el comentario que explica el arreglo nombra
    `minlength="8"` entero.
    """
    texto = re.sub(r"\{#.*?#\}", "", texto, flags=re.S)
    return re.sub(r"<!--.*?-->", "", texto, flags=re.S)


def _minimo_del_servidor() -> int:
    """El número que el servidor exige, leído de `users.py`.

    No se escribe a mano acá: si mañana la regla sube a 14, este test tiene que
    seguir el cambio en vez de convertirse en el quinto lugar que miente.
    """
    codigo = _RUTAS.read_text(encoding="utf-8")
    encontrados = {int(m) for m in re.findall(r"len\((?:new_)?password\) < (\d+)", codigo)}
    assert encontrados, "no se encontró ningún `len(password) < N` en users.py"
    assert len(encontrados) == 1, (
        f"users.py exige mínimos distintos según la ruta: {sorted(encontrados)}. "
        "Las dos rutas de contraseña tienen que pedir lo mismo"
    )
    return encontrados.pop()


def _inputs_de_password() -> list[tuple[str, str]]:
    """(archivo, etiqueta completa) de cada input de contraseña de los templates."""
    fuera = []
    for f in sorted(_TEMPLATES.rglob("*.html")):
        for tag in re.findall(r"<input\b[^>]*>", _sin_comentarios(f.read_text(encoding="utf-8"))):
            if 'type="password"' in tag:
                fuera.append((str(f.relative_to(_TEMPLATES)), tag))
    return fuera


def test_hay_formularios_de_password_que_revisar():
    """Si el barrido deja de encontrarlos, el test de abajo pasa vacío."""
    inputs = _inputs_de_password()
    assert len(inputs) >= 4, (
        f"sólo {len(inputs)} inputs de contraseña encontrados. O se borraron "
        "formularios, o el barrido dejó de verlos y este archivo se volvió "
        "decorativo"
    )


def test_ningun_formulario_pide_menos_que_el_servidor():
    minimo = _minimo_del_servidor()
    culpables = []
    for archivo, tag in _inputs_de_password():
        # El login no valida largo: pide la contraseña que ya existe, que puede
        # ser de cualquier largo. Lo que importa es que no PROMETA menos.
        m = re.search(r'minlength="(\d+)"', tag)
        if m and int(m.group(1)) < minimo:
            culpables.append(f"{archivo}: minlength={m.group(1)}")
    assert not culpables, (
        f"el servidor exige {minimo} caracteres y estos formularios prometen "
        f"menos: {culpables}. El navegador deja mandar, el servidor rechaza, y "
        "HTMX no hace swap en 4xx: el usuario aprieta y no pasa nada"
    )


def test_ningun_placeholder_promete_menos_que_el_servidor():
    """El texto que el usuario lee también es una promesa.

    El placeholder decía «mín. 8 caracteres» al lado de un `minlength` de 8:
    los dos mentían igual, y arreglar sólo el atributo habría dejado el cartel.
    """
    minimo = _minimo_del_servidor()
    culpables = []
    for archivo, tag in _inputs_de_password():
        ph = re.search(r'placeholder="([^"]*)"', tag)
        if not ph:
            continue
        for n in re.findall(r"\b(\d+)\s*caracteres", ph.group(1)):
            if int(n) < minimo:
                culpables.append(f"{archivo}: «{ph.group(1)}»")
    assert not culpables, (
        f"el servidor exige {minimo} caracteres y estos carteles prometen "
        f"menos: {culpables}"
    )


def test_el_cambio_de_password_no_contesta_con_un_4xx_mudo():
    """HTMX no hace swap en 4xx: un `raise` ahí es una pantalla que no reacciona.

    El alta de usuario ya renderizaba el error adentro del formulario. Esta
    ruta era la única que levantaba la excepción.
    """
    codigo = _RUTAS.read_text(encoding="utf-8")
    i = codigo.index("async def change_password")
    cuerpo = codigo[i:codigo.index("\n@router", i) if "\n@router" in codigo[i:] else len(codigo)]
    largo = cuerpo[:cuerpo.index("target = await user_management_service.change_password")]
    assert "error_password" in largo, (
        "`change_password` ya no renderiza el error de largo adentro de la fila"
    )
    assert "status_code=400" not in largo, (
        "`change_password` volvió a contestar 400 al error de largo. HTMX no "
        "hace swap en 4xx: el usuario aprieta el botón y no pasa nada"
    )

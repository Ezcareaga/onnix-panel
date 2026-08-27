"""El `.gitignore`, que no puede fallar en silencio.

En `.gitignore` el `#` **solo abre comentario al principio de la linea**. Un
patron con su explicacion pegada al final no ignora nada: el patron real pasa a
ser `.claude/skills/  # skills instaladas...`, que no matchea ningun archivo, y
git no avisa.

Paso el 2026-08-23: tres patrones estaban asi, y un `git add -A` metio los 147
archivos de una skill de terceros adentro de un commit de contraste. Dos de los
tres estaban tapados por `.git/info/exclude`, que es **local y no viaja con el
repo** — o sea que en el VPS o en otra maquina eran commiteables.

Este archivo mide contra el `git check-ignore` de verdad, no contra el texto:
lo que importa no es como se lee el patron sino si git lo aplica, **y si lo
aplica desde `.gitignore` y no desde un exclude local**.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[2]
_GITIGNORE = _RAIZ / ".gitignore"

# Rutas que NO pueden entrar al repo, con el porque. La lista es corta a
# proposito: son las que ya se colaron o las que serian un incidente.
NUNCA_SE_COMMITEAN = [
    (".claude/skills/impeccable/SKILL.md", "skills de terceros, 3,2 MB"),
    (".claude/settings.local.json", "permisos y MCP de una maquina"),
    (".claude/scheduled_tasks.lock", "lock de sesion"),
    ("rclone.conf", "credenciales — regla 9 del CLAUDE.md"),
    ("onnix_prod.dump", "un dump de produccion entero"),
    (".env", "secretos"),
    (".codegraph/db.sqlite", "indice local de 34 MB"),
]


# El espejo del bug de arriba: patrones que ignoran DE MAS. `package.json` sin
# barra inicial se aplica en cualquier directorio, y se llevaba puesto el
# manifiesto del proyecto de Remotion — un clon fresco no podia instalarlo.
# Un archivo que falta no rompe nada hasta que alguien clona.
SIEMPRE_SE_COMMITEAN = [
    ("tutoriales/video/package.json", "sin el, el proyecto de video no se instala"),
    ("tutoriales/video/package-lock.json", "la version exacta de Remotion"),
    ("tutoriales/video/src/tutorial/guion.ts", "los tutoriales son codigo"),
    ("panel/app/main.py", "la app"),
    ("scripts/run_suite.sh", "el gate"),
]


def _quien_lo_ignora(ruta: str) -> str | None:
    """El archivo:linea que hace que git ignore `ruta`, o None si no lo ignora."""
    r = subprocess.run(
        ["git", "check-ignore", "-v", "--no-index", ruta],
        cwd=_RAIZ, capture_output=True, text=True,
    )
    return r.stdout.split("\t")[0] if r.returncode == 0 else None


@pytest.mark.parametrize("ruta,porque", NUNCA_SE_COMMITEAN,
                         ids=[r for r, _ in NUNCA_SE_COMMITEAN])
def test_git_lo_ignora_de_verdad(ruta, porque):
    fuente = _quien_lo_ignora(ruta)
    assert fuente is not None, f"git NO ignora {ruta} ({porque})"


@pytest.mark.parametrize("ruta,porque", NUNCA_SE_COMMITEAN,
                         ids=[r for r, _ in NUNCA_SE_COMMITEAN])
def test_lo_ignora_el_gitignore_y_no_un_exclude_local(ruta, porque):
    """`.git/info/exclude` no viaja con el repo: lo que solo esta ahi es
    commiteable en el VPS y en la maquina del que clone."""
    fuente = _quien_lo_ignora(ruta)
    assert fuente and fuente.startswith(".gitignore"), (
        f"{ruta} lo frena `{fuente}`, no `.gitignore`. Si es un exclude local, "
        "en otra maquina el archivo entra al repo sin que nadie lo note"
    )


def test_ningun_patron_lleva_el_comentario_pegado_al_final():
    """La forma exacta del bug: `patron  # explicacion` no ignora nada."""
    culpables = []
    for n, linea in enumerate(_GITIGNORE.read_text(encoding="utf-8").splitlines(), 1):
        limpia = linea.strip()
        if not limpia or limpia.startswith("#"):
            continue
        # Un `#` escapado (`\#`) es literal y es valido; el resto es el bug.
        sin_escapar = limpia.replace(r"\#", "")
        if "#" in sin_escapar:
            culpables.append(f"  .gitignore:{n}  {linea}")
    assert not culpables, (
        "patrones con el comentario pegado al final — no ignoran nada, y git "
        "no avisa:\n" + "\n".join(culpables)
    )


@pytest.mark.parametrize("ruta,porque", SIEMPRE_SE_COMMITEAN,
                         ids=[r for r, _ in SIEMPRE_SE_COMMITEAN])
def test_no_se_ignora_lo_que_tiene_que_entrar(ruta, porque):
    """Ignorar de mas falla igual de callado que ignorar de menos.

    `git status` no lista lo ignorado, asi que el archivo simplemente no esta:
    el que clona se entera cuando algo no anda, no cuando falta.
    """
    fuente = _quien_lo_ignora(ruta)
    assert fuente is None, (
        f"{ruta} lo ignora `{fuente}` y tiene que entrar al repo ({porque})"
    )


# ---------------------------------------------------------------------------
# El caso del 2026-08-23: no fue el `.gitignore`, fue `.git/info/exclude`, y no
# se llevo puesto un archivo de build sino la documentacion del proyecto.
#
# El exclude tenia `docs/audit/` desde el 18/08, puesto para que el worktree de
# `master` no listara como untracked lo que «ya vive en dev». El agujero:
# `.git/info/exclude` es **por repositorio, no por worktree** — todos los
# worktrees comparten el mismo `.git`. Asi que la regla tambien aplicaba al
# arbol de `dev`, que es justo donde esos documentos se escriben, y doce
# documentos del 22 y 23/08 nunca se commitearon en ningun lado.
#
# Entre ellos `RETOMAR.md`, que `CLAUDE.md` nombra como la primera lectura al
# retomar. En un clon fresco la primera lectura apuntaba a un archivo ausente.
#
# Este test NO lleva una lista a mano de los documentos que tienen que estar:
# la **deriva de lo que los documentos referencian**. Una lista a mano no puede
# ver la eliminacion de uno de sus elementos (la trampa 6 del CLAUDE.md), y
# ademas habria que acordarse de sumarle cada documento nuevo — que es
# exactamente la clase de olvido que produjo el bug.
# ---------------------------------------------------------------------------

# De donde se sacan las referencias. Son los documentos que le dicen a alguien
# que retoma por donde empezar: si estos apuntan a algo ausente, el que retoma
# se queda sin punto de entrada.
_FUENTES_DE_REFERENCIAS = [
    "CLAUDE.md",
]

# Una ruta a documento entre backticks. Dos formas, porque los documentos usan
# las dos: con directorio (`docs/OPERACION.md`) y a secas (`RETOMAR.md`), que se
# lee relativa al directorio del documento que la nombra.
#
# La segunda forma NO es un detalle: `ESTADO_UI.md` dice «leer `RETOMAR.md`
# primero» sin prefijo, y la primera version de este test —que solo aceptaba
# rutas con directorio— se perdia justo el documento que motivo el test. Lo
# descubrio la mutacion de sanidad: destrackear `RETOMAR.md` la dejaba verde.
#
# En este fork el regex NO se limita a `.md`. El panel del que salio tenia
# `docs/` con doce documentos y CLAUDE.md era un indice de documentos; aca
# `docs/` no se copio, asi que si el regex solo mirara `.md` este test quedaria
# sostenido por una sola referencia — un piso de 1 no distingue «el regex
# funciona» de «el regex se rompio». Mirando tambien los archivos que CLAUDE.md
# manda leer (`tools.py`, `custom.css`, `base.py`...) el test prueba MAS: que
# cada ruta que el documento nombra exista y este trackeada.
_EXTENSIONES = "md|py|js|mjs|css|html|yml|yaml|sh|conf|sql|jsonl|svg|ini|toml"
_REF_MD = re.compile(rf"`((?:[\w.-]+/)*[\w.-]+\.(?:{_EXTENSIONES}))`")


def _sin_comentarios(texto: str) -> str:
    """Saca comentarios HTML y de Jinja.

    La trampa propia de este repo: el comentario que explica un patron lo
    contiene. Un documento puede citar la ruta de algo borrado a proposito
    dentro de un bloque tachado o comentado, y eso no es una referencia viva.
    """
    texto = re.sub(r"<!--.*?-->", "", texto, flags=re.S)
    return re.sub(r"\{#.*?#\}", "", texto, flags=re.S)


def _resolver_nombre_suelto(nombre: str, dir_fuente: pathlib.PurePosixPath) -> str:
    """Un `RETOMAR.md` a secas, a la ruta desde la raiz.

    Los documentos nombran a secas tanto al hermano (`RETOMAR.md`, al lado de
    `ESTADO_UI.md`) como al de la raiz (`CLAUDE.md`) y al que vive en otro lado
    (`ui.md`, que esta en `.claude/rules/`). Se prueba hermano, raiz, y recien
    entonces se busca por nombre en el arbol.

    Si no aparece en ningun lado se devuelve la variante hermana: es una
    referencia rota de verdad y `test_el_documento_referenciado_existe` la
    reporta con el nombre que el documento uso.
    """
    hermano = str(dir_fuente / nombre)
    for cand in (hermano, nombre):
        if (_RAIZ / cand).exists():
            return cand
    for hallado in _RAIZ.glob(f"**/{nombre}"):
        if ".git/" in str(hallado) or "node_modules" in str(hallado):
            continue
        return str(hallado.relative_to(_RAIZ))
    return hermano


def _referencias() -> list[tuple[str, str]]:
    """(ruta referenciada desde la raiz, quien la referencia), sin repetir."""
    vistas: dict[str, str] = {}
    for fuente in _FUENTES_DE_REFERENCIAS:
        p = _RAIZ / fuente
        if not p.exists():
            continue
        dir_fuente = pathlib.PurePosixPath(fuente).parent
        for cruda in _REF_MD.findall(_sin_comentarios(p.read_text(encoding="utf-8"))):
            ruta = cruda if "/" in cruda else _resolver_nombre_suelto(cruda, dir_fuente)
            vistas.setdefault(ruta, fuente)
    return sorted(vistas.items())


def _esta_trackeado(ruta: str) -> bool:
    r = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ruta],
        cwd=_RAIZ, capture_output=True, text=True,
    )
    return r.returncode == 0


def test_hay_referencias_que_revisar():
    """Si el regex deja de matchear, los dos tests de abajo pasan vacios.

    Un test que no recorre nada es verde y no prueba nada. El piso no es un
    numero redondo elegido a dedo: `CLAUDE.md` sola ya nombra mas de diez
    documentos en su seccion «Donde esta lo demas».
    """
    refs = _referencias()
    assert len(refs) >= 10, (
        f"solo {len(refs)} referencias encontradas en {_FUENTES_DE_REFERENCIAS}. "
        "O se borro documentacion, o el regex dejo de matchear y estos tests "
        "se volvieron decorativos"
    )


@pytest.mark.parametrize("ruta,fuente", _referencias(),
                         ids=[r for r, _ in _referencias()])
def test_el_documento_referenciado_existe(ruta, fuente):
    assert (_RAIZ / ruta).exists(), (
        f"`{fuente}` manda leer `{ruta}` y el archivo no esta en el arbol"
    )


@pytest.mark.parametrize("ruta,fuente", _referencias(),
                         ids=[r for r, _ in _referencias()])
def test_el_documento_referenciado_esta_en_el_repo(ruta, fuente):
    """Existir en el disco de una laptop no es existir.

    Este es el assert que habria fallado el 22/08 y ahorrado doce documentos
    viviendo en un solo disco.
    """
    if not (_RAIZ / ruta).exists():
        pytest.skip(f"{ruta} no existe; lo reporta test_el_documento_referenciado_existe")
    assert _esta_trackeado(ruta), (
        f"`{fuente}` manda leer `{ruta}`, el archivo esta en el disco y git NO "
        f"lo trackea. Lo frena `{_quien_lo_ignora(ruta)}`. En un clon fresco "
        "ese documento no existe"
    )

"""La cola de fotos baja lo más nuevo primero, porque nunca termina.

`scripts/download_images.py` ordenaba `ORDER BY source, id`, que es lo más viejo
primero: un alta nueva es el `id` más alto de su fuente, o sea el **último**
lugar de la cola.

Eso solo no rompe nada. Lo que lo rompe es la otra mitad: el cron corre una vez
por día con `timeout 80m`, y a ritmo medido —7,6 a 9,9 propiedades por minuto—
vaciar la cola de 6.807 pendientes son 11 a 15 horas. **Ninguna corrida del cron
terminó nunca** desde que se instaló el crontab el 2026-08-20: el 21/08 llegó a
700 de 6.807 y el 22/08 a 600 de 6.158.

Con la cola al revés, lo que queda afuera del timeout es siempre lo recién dado
de alta — que es exactamente lo que encabeza el listado. Medido el 2026-08-24
sobre las altas de los últimos 7 días (n=2.178): **mediana de 65 horas** hasta la
foto, y sólo el 10 % dentro de las 12.

Es una regresión. El `CHANGELOG` del 2026-06-11 ya lo había arreglado, con estas
palabras: «closing the up-to-24h no-photo window for newly scraped props that
headline the public portal». El crontab del VPS nuevo volvió al horario viejo.

**Este test cubre la mitad que vive en el código.** La otra mitad es la
frecuencia del cron y está en `docs/OPERACION.md`, porque el crontab no se
versiona.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parents[2]
_FUENTE = REPO / "scripts" / "download_images.py"


def _sin_comentarios(texto: str) -> str:
    """Sin comentarios `#` ni docstrings — la trampa propia de este repo.

    El comentario que explica el arreglo nombra `ORDER BY source, id` entero.
    """
    texto = re.sub(r'"""(?:.|\n)*?"""', "", texto)
    return re.sub(r"(?m)#.*$", "", texto)


def _cargar():
    spec = importlib.util.spec_from_file_location("_dl_img_orden", _FUENTE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_dl_img_orden"] = mod
    spec.loader.exec_module(mod)
    return mod


def _sql_ejecutado(source=None, limit=None) -> str:
    """El SQL real que arma `fetch_pending`, con un cursor de mentira."""
    mod = _cargar()
    cur = MagicMock()
    cur.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    mod.fetch_pending(conn, source, limit)
    return cur.execute.call_args[0][0]


def test_la_cola_arranca_por_lo_mas_nuevo():
    sql = _sql_ejecutado()
    assert "ORDER BY created_at DESC" in sql, (
        f"la cola no ordena por fecha descendente. SQL: {sql!r}"
    )


def test_la_cola_no_vuelve_a_ordenar_por_id():
    """El orden viejo, nombrado exacto.

    `source, id` es lo más viejo primero. Si vuelve, lo recién dado de alta
    vuelve al fondo y una corrida cortada por timeout no lo alcanza nunca.
    """
    sql = _sql_ejecutado()
    orden = sql[sql.index("ORDER BY"):]
    assert "source, id" not in orden, (
        f"la cola volvió a `ORDER BY source, id`: {orden.strip()!r}. Es la "
        "regresión que el CHANGELOG del 2026-06-11 ya había arreglado una vez"
    )


def test_el_orden_no_depende_de_los_filtros():
    """Con `source` o con `limit`, la cola sigue arrancando por lo más nuevo.

    `source` agrega un `AND` **antes** del ORDER BY y `limit` un `LIMIT`
    después: si alguno se concatenara en el orden equivocado, el SQL saldría
    roto o el orden se perdería para el caso filtrado, que es justo el que se usa
    a mano cuando hay que recuperar un portal.
    """
    for kwargs in ({"source": "remax"}, {"limit": 50}, {"source": "remax", "limit": 50}):
        sql = _sql_ejecutado(**kwargs)
        assert "ORDER BY created_at DESC" in sql, f"con {kwargs}: {sql!r}"
        if "limit" in kwargs:
            assert sql.index("ORDER BY") < sql.index("LIMIT"), (
                f"con {kwargs} el LIMIT quedó antes del ORDER BY: {sql!r}"
            )


def test_la_fuente_no_lleva_el_orden_viejo_escrito():
    """Ni siquiera fuera de esta función.

    Filtrando comentarios y docstrings, para que la explicación del arreglo
    —que cita el patrón prohibido— no dispare el test.
    """
    codigo = _sin_comentarios(_FUENTE.read_text(encoding="utf-8"))
    assert "ORDER BY source, id" not in codigo, (
        "quedó un `ORDER BY source, id` en el script"
    )

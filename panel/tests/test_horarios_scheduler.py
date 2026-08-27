"""Las tareas del scheduler corren a la hora que su comentario promete.

Cuatro tareas estaban escritas con la hora **UTC** —«08:00 PYT = 12:00 UTC»,
`hour=12`— sobre un `AsyncIOScheduler` construido **sin `timezone=`**, que por lo
tanto tomaba la zona del proceso: `America/Asuncion`. Ese 12 se interpretaba
como hora local, así que todo corría **cuatro horas tarde**.

No es deducción. Está en el log de producción:

    [2026-08-22T12:00:01-0300] Job executed — {"task": "daily_report", ...}
    [2026-08-23T12:00:00-0300] Job executed — {"task": "daily_report", ...}

El reporte diario «de las 08:00» llegaba a las 12:00, todos los días, durante
meses. Y nadie lo notó porque salía con `email_sent: False, tg_sent: False` —
las dos credenciales de aviso están rotas.

Este archivo ata las dos mitades del arreglo: que el huso sea **explícito** —para
que los horarios dejen de depender de la zona del servidor— y que el número
escrito coincida con lo que el comentario y el log prometen.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parent.parent / "app"
_LIFESPAN = _APP / "bot" / "scheduler" / "lifespan.py"
_SERVICE = _APP / "bot" / "scheduler" / "scheduler_service.py"

# (tarea, hora de Paraguay). El número sale de lo que cada tarea dice que hace,
# no de lo que hoy tiene escrito: si alguien mueve una hora, tiene que mover
# también su promesa acá y decir por qué.
# `followup_sender` se fue con el bot: mandaba plantillas de seguimiento sola
# a las 10:00, y Onnix manda las plantillas a mano desde el hilo.
HORARIOS_PYT = [
    ("daily_report", 8),
    ("cleanup_inactive_refs", 2),
    ("verification_scraper", 12),
]


def _sin_comentarios(texto: str) -> str:
    """Sin comentarios `#` ni docstrings.

    La trampa propia de este repo: el comentario que explica el arreglo cita
    `hour=12` y «12:00 UTC» enteros.
    """
    texto = re.sub(r'"""(?:.|\n)*?"""', "", texto)
    return re.sub(r"(?m)#.*$", "", texto)


def _bloque(tarea: str) -> str:
    """El fragmento de `lifespan.py` que registra esa tarea, sin comentarios."""
    codigo = _LIFESPAN.read_text(encoding="utf-8")
    i = codigo.index(f"Register {tarea}")
    fin = codigo.index("logger.info", i)
    return _sin_comentarios(codigo[i:fin])


def test_el_scheduler_fija_su_huso_en_vez_de_heredarlo():
    """La mitad que impide que el bug vuelva por otra puerta.

    Con el huso heredado del sistema, cambiar la zona del servidor mueve todas
    las tareas sin que nadie toque una línea de código.
    """
    codigo = _sin_comentarios(_SERVICE.read_text(encoding="utf-8"))
    i = codigo.index("AsyncIOScheduler(")
    j = codigo.index(")", codigo.index("job_defaults", i))
    assert "timezone=" in codigo[i:j], (
        "`AsyncIOScheduler` se construye sin `timezone=`: hereda la zona del "
        "proceso. Es exactamente como nacio el bug de las cuatro horas"
    )


@pytest.mark.parametrize("tarea,hora", HORARIOS_PYT, ids=[t for t, _ in HORARIOS_PYT])
def test_la_hora_escrita_es_la_hora_de_paraguay(tarea, hora):
    bloque = _bloque(tarea)
    m = re.search(r"hour=(\d+)", bloque)
    assert m, f"`{tarea}` no fija `hour=`"
    assert int(m.group(1)) == hora, (
        f"`{tarea}` corre a las {m.group(1)}:00 de Paraguay y su promesa es "
        f"{hora}:00. Si la hora cambio a proposito, cambia tambien "
        "HORARIOS_PYT y deci por que"
    )


@pytest.mark.parametrize("tarea,hora", HORARIOS_PYT, ids=[t for t, _ in HORARIOS_PYT])
def test_el_log_no_promete_una_hora_distinta_de_la_que_corre(tarea, hora):
    """El `logger.info` es lo único que alguien lee para saber cuándo corre.

    Decía «cron 12:00 UTC / 08:00 PYT» sobre una tarea que corría 12:00 PYT:
    las dos mitades del cartel eran falsas al mismo tiempo.
    """
    codigo = _LIFESPAN.read_text(encoding="utf-8")
    m = re.search(rf'logger\.info\("Task registered: {tarea} \(([^"]*)\)"\)', codigo)
    assert m, f"no se encontro el log de registro de `{tarea}`"
    cartel = m.group(1)
    horas = {int(h) for h in re.findall(r"\b(\d{1,2}):00", cartel)}
    assert horas == {hora}, (
        f"`{tarea}` corre a las {hora}:00 PYT y su log dice «{cartel}». "
        "Un cartel que nombra dos horas distintas miente en una de las dos"
    )
    assert "UTC" not in cartel, (
        f"el log de `{tarea}` sigue nombrando UTC: «{cartel}». El scheduler "
        "corre en hora de Paraguay"
    )

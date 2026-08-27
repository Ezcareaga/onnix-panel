"""El reporte diario rotula con la fecha de Paraguay.

`daily_report` tenía el huso escrito a mano como `timezone(timedelta(hours=-4))`.
Estaba mal por dos motivos: Paraguay abolió el horario de verano y quedó en
UTC-3, y un offset fijo no sobrevive a un cambio de reglas. El huso sale ahora
de `app.tz.PYT`, que es un `ZoneInfo`.

El caso que separa -3 de -4 es angosto: entre las 03:00 y las 04:00 UTC. Un
test a mediodía pasa verde con cualquiera de los dos offsets y no prueba nada.
"""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.tz import PYT

_FUENTE = (
    Path(__file__).parent.parent.parent
    / "app/bot/scheduler/tasks/daily_report.py"
)


def _codigo_sin_comentarios() -> str:
    """El fuente sin comentarios ni docstrings.

    El comentario que explica este mismo fix nombra el offset viejo, así que
    un assert por substring sobre el archivo crudo encontraría el patrón
    prohibido en la explicación de por qué se fue.

    El filtro va por ``ast``, no por regex: este módulo arma el HTML del mail
    con colores hex adentro de strings, y un ``#.*$`` los corta al medio y
    deja un literal sin cerrar.
    """
    arbol = ast.parse(_FUENTE.read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        cuerpo = getattr(nodo, "body", [])
        if (cuerpo and isinstance(cuerpo[0], ast.Expr)
                and isinstance(cuerpo[0].value, ast.Constant)
                and isinstance(cuerpo[0].value.value, str)):
            cuerpo.pop(0)
            if not cuerpo:
                cuerpo.append(ast.Pass())
    ast.fix_missing_locations(arbol)
    # unparse reconstruye desde el árbol: los comentarios no llegan hasta acá.
    return ast.unparse(arbol)


def test_la_medianoche_paraguaya_cae_donde_debe():
    """03:30 UTC del 22 es 00:30 del 22 en Paraguay (UTC-3).

    Con el offset viejo de -4 daría las 23:30 del 21: otro día, y el reporte
    saldría rotulado con la fecha de ayer. Es la única franja donde los dos
    offsets difieren de día.
    """
    momento = datetime(2026, 8, 22, 3, 30, tzinfo=timezone.utc)
    assert momento.astimezone(PYT).strftime("%Y-%m-%d") == "2026-08-22"

    viejo = timezone(timedelta(hours=-4))
    assert momento.astimezone(viejo).strftime("%Y-%m-%d") == "2026-08-21", (
        "si este assert falla, el caso elegido dejó de distinguir -3 de -4 y "
        "el test de arriba pasaría con el bug puesto"
    )


def test_paraguay_no_tiene_horario_de_verano():
    """Enero y julio dan el mismo offset: si algún día vuelve el DST, este
    test avisa antes de que las series se muevan solas."""
    enero = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc).astimezone(PYT)
    julio = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc).astimezone(PYT)
    assert enero.utcoffset() == julio.utcoffset() == timedelta(hours=-3)


def test_el_reporte_no_arma_el_huso_a_mano():
    codigo = _codigo_sin_comentarios()
    assert "timedelta(hours=-4)" not in codigo, (
        "el huso de Paraguay no se escribe a mano: -4 quedó viejo cuando se "
        "abolió el horario de verano"
    )
    assert "timedelta(hours=-3)" not in codigo, (
        "aunque -3 sea el offset correcto hoy, un número fijo no sobrevive a "
        "un cambio de reglas; el nombre de la zona sí"
    )
    assert "datetime.now(PYT)" in codigo, (
        "la fecha del reporte tiene que salir del ZoneInfo de app.tz"
    )

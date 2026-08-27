#!/usr/bin/env python3
"""Backfill de atributos desde la descripción, para los avisos ya cargados.

El extractor quedó cableado en la ingesta (``upsert_property``), pero eso sólo
arregla lo que entra de ahora en más. Este script hace la pasada sobre lo que
ya está en la tabla.

Usa EXACTAMENTE la misma lógica que la ingesta (``enrich_upsert_params``), así
que no puede divergir: si mañana se ajusta un regex, backfill e ingesta cambian
juntos.

Reglas (heredadas):
  - sólo se rellenan columnas NULL — el dato del portal nunca se pisa;
  - los límites de sanidad descartan capturas absurdas;
  - lo que no es columna (piso, amenities) va a raw_data;
  - raw_data.extracted_fields registra qué columnas salieron del texto.

Modos:
  --validate   NO escribe. Mide el acierto del regex contra los avisos que YA
               tienen el campo estructurado: corre la extracción ignorando el
               valor real y compara. Es la validación gratis, sin etiquetar a
               mano. Correr esto ANTES del --apply.
  (default)    Dry-run: recorre, calcula y reporta cuánto rellenaría. No escribe.
  --apply      Escribe.

Uso:
  python scripts/backfill_description_extraction.py --validate
  python scripts/backfill_description_extraction.py                 # dry-run
  python scripts/backfill_description_extraction.py --apply
  python scripts/backfill_description_extraction.py --apply --source onnixpy
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

# Igual que tests/conftest.py: scrapers/ al path para 'from shared.X import ...'
_SCRAPERS = str(Path(__file__).resolve().parent.parent / "scrapers")
if _SCRAPERS not in sys.path:
    sys.path.insert(0, _SCRAPERS)

import psycopg.types.json  # noqa: E402

from shared.config import setup_logging  # noqa: E402
from shared.db import _UPSERT_COLUMNS, get_connection  # noqa: E402
from shared.description_extractor import (  # noqa: E402
    extract_from_description,
    enrich_upsert_params,
)

logger = logging.getLogger("backfill_description")

# Columnas que el extractor puede rellenar y que además se pueden validar
# contra el dato del portal (las que existen como columna).
VALIDATABLE = (
    "bedrooms", "bathrooms", "parking_spaces", "total_area_m2", "built_area_m2",
    "neighborhood",
)

# Campos que no son un número: se comparan por texto, no por mayor/menor.
_TEXTUALES = ("neighborhood",)

# Se lee y se escribe sólo esto: nada de traer la fila entera.
# title y city entran porque el extractor los lee: el título es texto del mismo
# aviso, y sin la ciudad no se puede resolver el barrio.
_READ_COLUMNS = (
    "id", "external_id", "title", "description", "city", "raw_data",
    "bedrooms", "bathrooms", "parking_spaces", "total_area_m2", "built_area_m2",
    "neighborhood",
)

BATCH = 500


# El portal escribe «Barrio Jara» donde el gazetteer dice «Jara», y «Carmelitas»
# donde dice «Las Lomas». Comparar en crudo cuenta esas dos como errores del
# regex: la validación decía 74,6% de acierto donde la medición por regla da 87%.
_RE_CUE_AL_COMPARAR = re.compile(r"^(?:barrio|bo\.?|b°)\s+")


def _alias_de_barrios() -> dict[str, str]:
    ruta = Path(__file__).resolve().parent.parent / "data" / "geografia" / "aliases.json"
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f).get("barrios", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


_ALIAS_BARRIOS = _alias_de_barrios()


def _clave_texto(valor) -> str:
    """Compara barrios como los compara la búsqueda: sin acentos, sin caja, con alias."""
    txt = unicodedata.normalize("NFKD", str(valor).strip().lower())
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    txt = _RE_CUE_AL_COMPARAR.sub("", txt)
    return _ALIAS_BARRIOS.get(txt, txt)


def plan_row_update(row: dict) -> tuple[dict, list[str]]:
    """Qué habría que escribir en esta fila. Función pura, sin DB.

    Devuelve ``(updates, filled)``: el dict a actualizar (columnas + raw_data si
    cambió) y la lista de columnas rellenadas desde el texto. ``({}, [])`` si no
    hay nada que hacer.

    raw_data se escribe aunque no se rellene ninguna columna: el texto puede
    aportar sólo amenities o piso, y la ingesta los guarda igual. Si acá se
    descartaran, backfill e ingesta dejarían filas distintas para el mismo aviso.
    """
    enriched = enrich_upsert_params(dict(row), _UPSERT_COLUMNS)
    filled = [c for c in VALIDATABLE if row.get(c) is None and enriched.get(c) is not None]

    updates = {c: enriched[c] for c in filled}
    raw = enriched.get("raw_data")
    if raw is not None:
        # Comparar por CONTENIDO, no por identidad: enrich_upsert_params siempre
        # devuelve un objeto nuevo, así que comparar referencias haría que cada
        # corrida reescriba las mismas filas (el backfill dejaría de ser
        # idempotente y updated_at se movería sin motivo).
        new_obj = raw.obj if hasattr(raw, "obj") else raw
        old = row.get("raw_data")
        old_obj = old.obj if hasattr(old, "obj") else old
        if new_obj != old_obj:
            # El UPDATE necesita el wrapper Jsonb sí o sí.
            updates["raw_data"] = psycopg.types.json.Jsonb(new_obj)
    return updates, filled


def iter_rows(conn, source: str | None, limit: int | None):
    """Recorre por keyset (id > último) — sin OFFSET, sin cursor colgado."""
    cols = ", ".join(_READ_COLUMNS)
    where = ["(description IS NOT NULL OR title IS NOT NULL)"]
    args: list = []
    if source:
        where.append("source = %s")
        args.append(source)
    sql = (
        f"SELECT {cols} FROM properties "
        f"WHERE {' AND '.join(where)} AND id > %s ORDER BY id LIMIT %s"
    )
    last_id, seen = 0, 0
    while True:
        size = BATCH if limit is None else min(BATCH, limit - seen)
        if size <= 0:
            return
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(sql, (*args, last_id, size))
            rows = cur.fetchall()
        if not rows:
            return
        for row in rows:
            yield row
        last_id = rows[-1]["id"]
        seen += len(rows)


def run_backfill(source: str | None, limit: int | None, apply: bool) -> int:
    filled_counter: Counter[str] = Counter()
    rows_touched = 0
    scanned = 0

    with get_connection() as conn:
        pending: list[tuple[int, dict]] = []
        for row in iter_rows(conn, source, limit):
            scanned += 1
            updates, filled = plan_row_update(row)
            if not updates:
                continue
            rows_touched += 1
            if filled:
                filled_counter.update(filled)
            else:
                filled_counter["(sólo raw_data: amenities/piso)"] += 1
            pending.append((row["id"], updates))

            if apply and len(pending) >= BATCH:
                _write(conn, pending)
                pending.clear()

        if apply and pending:
            _write(conn, pending)
        if apply:
            conn.commit()

    modo = "APLICADO" if apply else "DRY-RUN (no se escribió nada)"
    print(f"\n=== Backfill {modo} ===")
    print(f"Avisos con descripción recorridos: {scanned}")
    print(f"Avisos que se rellenarían/rellenaron: {rows_touched}")
    for col, count in filled_counter.most_common():
        print(f"  {col:32} +{count}")
    if not rows_touched:
        print("  (nada que rellenar)")
    return 0


def _write(conn, pending: list[tuple[int, dict]]) -> None:
    with conn.cursor() as cur:
        for prop_id, updates in pending:
            sets = ", ".join(f"{c} = %({c})s" for c in updates)
            cur.execute(
                f"UPDATE properties SET {sets}, updated_at = NOW() WHERE id = %(_id)s",
                {**updates, "_id": prop_id},
            )
    logger.info("Backfill batch written - %s", {"rows": len(pending)})


def run_validate(source: str | None, limit: int | None) -> int:
    """Acierto del regex contra el dato del portal. No escribe nada."""
    stats = {c: Counter() for c in VALIDATABLE}

    with get_connection() as conn:
        for row in iter_rows(conn, source, limit):
            # Se fuerza la extracción ignorando lo que ya está en la fila.
            extracted = extract_from_description(
                row["description"],
                existing_values={},
                title=row.get("title"),
                city=row.get("city"),
            )
            for col in VALIDATABLE:
                real = row.get(col)
                guess = extracted.get(col)
                if real is None:
                    continue
                if guess is None:
                    stats[col]["sin_captura"] += 1
                elif col in _TEXTUALES:
                    igual = _clave_texto(guess) == _clave_texto(real)
                    stats[col]["acierto" if igual else "distinto"] += 1
                elif type(real)(guess) == real:
                    stats[col]["acierto"] += 1
                elif guess > real:
                    stats[col]["sobreestima"] += 1
                else:
                    stats[col]["subestima"] += 1

    print("\n=== Validación del extractor contra el dato del portal ===")
    print("(sólo avisos que YA tienen el campo — el regex corre a ciegas y se compara)\n")
    print(f"{'campo':16} {'con dato':>9} {'acierto':>9} {'%':>6} {'sobre':>7} {'sub':>6} {'sin captura':>12}")
    for col in VALIDATABLE:
        c = stats[col]
        total = sum(c.values())
        if not total:
            continue
        pct = 100.0 * c["acierto"] / total
        errado = c["distinto"] if col in _TEXTUALES else c["sobreestima"]
        otro = "" if col in _TEXTUALES else f"{c['subestima']:>6}"
        print(
            f"{col:16} {total:>9} {c['acierto']:>9} {pct:>5.1f}% "
            f"{errado:>7} {otro:>6} {c['sin_captura']:>12}"
        )
    print("\n(en los campos de texto la columna 'sobre' es 'distinto': el regex "
          "dijo otro barrio que el portal)")
    print(
        "\nLectura: 'sin captura' no es error (el texto no lo dice, por eso el campo "
        "estructurado existe).\nLo que importa es acierto vs sobre/sub: si un campo "
        "erra mucho, calibrar el regex ANTES de --apply."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="escribe (por defecto es dry-run)")
    parser.add_argument("--validate", action="store_true", help="mide el acierto del regex, no escribe")
    parser.add_argument("--source", help="limitar a un scraper (onnixpy, remax, …)")
    parser.add_argument("--limit", type=int, help="cortar después de N avisos")
    args = parser.parse_args()

    setup_logging("INFO")
    if args.validate:
        return run_validate(args.source, args.limit)
    return run_backfill(args.source, args.limit, args.apply)


if __name__ == "__main__":
    sys.exit(main())

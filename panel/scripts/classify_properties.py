#!/usr/bin/env python3
"""
Migración masiva: clasifica property_type_normalized en todas las propiedades activas.

Etapas:
1. UPDATE masivo CASE WHEN (reglas determinísticas) — ~18.8K propiedades
2. Override con seed de audit_classifications.jsonl — 486 propiedades
3. Reporte final de cobertura

Idempotente: cada etapa puede re-ejecutarse sin duplicar trabajo.

Uso:
    docker exec onnix-panel-dev python /app/scripts/classify_properties.py
    docker exec onnix-panel-dev python /app/scripts/classify_properties.py --dry-run
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEED_PATH = Path("/home/onnix/docs/audit_classifications.jsonl")

# Deterministic remap: lower(property_type) → property_type_normalized id.
# Covers all 27 distinct values observed in staging (2026-04-14).
REMAP_SQL = """
UPDATE properties
SET property_type_normalized = CASE
    WHEN lower(property_type) = 'casa'                              THEN 1
    WHEN lower(property_type) = 'departamento'                      THEN 2
    WHEN lower(property_type) = 'departamento-en-pozo'              THEN 2
    WHEN lower(property_type) = 'casa-duplex'                       THEN 3
    WHEN lower(property_type) = 'casa-en-condominio'                THEN 1
    WHEN lower(property_type) = 'terreno'                           THEN 4
    WHEN lower(property_type) = 'oficina'                           THEN 5
    WHEN lower(property_type) = 'oficinas'                          THEN 5
    WHEN lower(property_type) = 'local'                             THEN 6
    WHEN lower(property_type) = 'deposito'                          THEN 7
    WHEN lower(property_type) = 'nave'                              THEN 7
    WHEN lower(property_type) = 'bodega'                            THEN 7
    WHEN lower(property_type) = 'fabrica'                           THEN 7
    WHEN lower(property_type) = 'quinta'                            THEN 8
    WHEN lower(property_type) = 'campo'                             THEN 9
    WHEN lower(property_type) = 'propiedad-agricola'                THEN 9
    WHEN lower(property_type) = 'hacienda'                          THEN 9
    WHEN lower(property_type) = 'livestock farm'                    THEN 9
    WHEN lower(property_type) = 'edificio'                          THEN 10
    WHEN lower(property_type) = 'estacionamiento'                   THEN 99
    WHEN lower(property_type) = 'fraccionamiento'                   THEN 99
    WHEN lower(property_type) = 'inmueble-productivo'               THEN 99
    WHEN lower(property_type) = 'casa para estudiantes'             THEN 1
    WHEN lower(property_type) = 'departamento con jardin'           THEN 2
    WHEN lower(property_type) = 'departamento con servicio de hotel' THEN 2
    WHEN lower(property_type) = 'restaurant with rooms'             THEN 99
    ELSE NULL
END
WHERE is_active = true
  AND property_type IS NOT NULL
  AND trim(property_type) != '';
"""

COVERAGE_SQL = """
SELECT
    COUNT(*)                                                      AS total,
    COUNT(*) FILTER (WHERE property_type_normalized IS NOT NULL)  AS clasificadas,
    COUNT(*) FILTER (WHERE property_type_normalized IS NULL)      AS pendientes,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE property_type_normalized IS NOT NULL)
        / NULLIF(COUNT(*), 0),
        2
    )                                                             AS cobertura_pct
FROM properties
WHERE is_active = true;
"""

DISTRIBUTION_SQL = """
SELECT
    pt.display_name,
    pt.id,
    COUNT(p.id) AS count
FROM properties p
JOIN property_types pt ON p.property_type_normalized = pt.id
WHERE p.is_active = true
GROUP BY pt.display_name, pt.id
ORDER BY count DESC;
"""


# ---------------------------------------------------------------------------
# Step 1: Deterministic remap
# ---------------------------------------------------------------------------


async def run_remap(db: AsyncSession, dry_run: bool) -> int:
    """Apply CASE WHEN UPDATE for all known property_type values.

    Args:
        db: Active async SQLAlchemy session.
        dry_run: If True, roll back after counting affected rows.

    Returns:
        Number of rows that would be (or were) updated.
    """
    result = await db.execute(text(REMAP_SQL))
    rows_affected: int = result.rowcount  # type: ignore[attr-defined]

    if dry_run:
        await db.rollback()
        print(f"[DRY-RUN] Paso 1 — remap determinístico: {rows_affected} filas afectadas (no aplicado)")
    else:
        await db.commit()
        print(f"[OK] Paso 1 — remap determinístico: {rows_affected} filas actualizadas")

    return rows_affected


# ---------------------------------------------------------------------------
# Step 2: Seed override from audit_classifications.jsonl
# ---------------------------------------------------------------------------


def load_seed(path: Path) -> list[dict[str, Any]]:
    """Load and validate seed entries from the audit JSONL file.

    Args:
        path: Path to audit_classifications.jsonl.

    Returns:
        List of validated seed dicts with 'id' and 'codigo_llm' keys.

    Raises:
        FileNotFoundError: If the seed file does not exist.
        ValueError: If any entry is missing required fields.
    """
    if not path.exists():
        raise FileNotFoundError(f"Seed file not found: {path}")

    entries: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        for field in ("id", "codigo_llm", "confianza"):
            if field not in entry:
                raise ValueError(f"Line {lineno}: missing field '{field}'")
        entries.append(entry)

    return entries


async def run_seed_override(db: AsyncSession, dry_run: bool) -> int:
    """Override property_type_normalized using the audit seed.

    Builds a single bulk UPDATE with CASE WHEN for efficiency instead of
    issuing one UPDATE per property.

    Args:
        db: Active async SQLAlchemy session.
        dry_run: If True, roll back after counting affected rows.

    Returns:
        Number of rows updated (or that would be updated).
    """
    entries = load_seed(SEED_PATH)

    if not entries:
        print("[WARN] Seed file is empty — skipping paso 2")
        return 0

    # Build: UPDATE properties SET property_type_normalized = CASE
    #            WHEN id = 379 THEN 4
    #            WHEN id = 32825 THEN 9
    #            ...
    #        END
    #        WHERE id IN (379, 32825, ...) AND is_active = true;
    case_clauses = "\n            ".join(
        f"WHEN id = {e['id']} THEN {e['codigo_llm']}" for e in entries
    )
    ids_csv = ", ".join(str(e["id"]) for e in entries)

    seed_sql = f"""
UPDATE properties
SET property_type_normalized = CASE
            {case_clauses}
        END
WHERE id IN ({ids_csv})
  AND is_active = true;
"""

    result = await db.execute(text(seed_sql))
    rows_affected: int = result.rowcount  # type: ignore[attr-defined]

    if dry_run:
        await db.rollback()
        print(f"[DRY-RUN] Paso 2 — seed override: {rows_affected} filas afectadas (no aplicado)")
    else:
        await db.commit()
        print(f"[OK] Paso 2 — seed override: {rows_affected} filas actualizadas")

    return rows_affected


# ---------------------------------------------------------------------------
# Step 3: Coverage report
# ---------------------------------------------------------------------------


async def report_coverage(db: AsyncSession) -> tuple[int, int, float]:
    """Print coverage stats and type distribution.

    Args:
        db: Active async SQLAlchemy session.

    Returns:
        Tuple of (clasificadas, pendientes, cobertura_pct).
    """
    row = (await db.execute(text(COVERAGE_SQL))).mappings().one()

    total: int = row["total"]
    clasificadas: int = row["clasificadas"]
    pendientes: int = row["pendientes"]
    cobertura: float = float(row["cobertura_pct"] or 0)

    print("\n=== COBERTURA FINAL ===")
    print(f"  Total activas : {total:,}")
    print(f"  Clasificadas  : {clasificadas:,}")
    print(f"  Pendientes    : {pendientes:,}")
    print(f"  Cobertura     : {cobertura:.2f}%")

    dist_rows = (await db.execute(text(DISTRIBUTION_SQL))).mappings().all()
    if dist_rows:
        print("\n=== DISTRIBUCIÓN POR TIPO ===")
        for r in dist_rows:
            print(f"  [{r['id']:>2}] {r['display_name']:<15}  {r['count']:>6,}")

    if pendientes > 0:
        # Show a sample of unclassified for debugging
        sample = (
            await db.execute(
                text(
                    "SELECT id, source, property_type "
                    "FROM properties "
                    "WHERE is_active = true AND property_type_normalized IS NULL "
                    "LIMIT 10"
                )
            )
        ).mappings().all()
        print(f"\n=== MUESTRA DE {len(sample)} PENDIENTES ===")
        for r in sample:
            print(f"  id={r['id']}  source={r['source']}  type='{r['property_type']}'")

    if cobertura < 98.5:
        print(f"\n[WARN] Cobertura {cobertura:.2f}% < 98.5% — verificar pendientes")
    else:
        print(f"\n[OK] Cobertura {cobertura:.2f}% >= 98.5% — criterio cumplido")

    return clasificadas, pendientes, cobertura


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(dry_run: bool) -> None:
    """Execute all migration steps.

    Args:
        dry_run: If True, all DB changes are rolled back before committing.
    """
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    if dry_run:
        print("[DRY-RUN MODE] Ningún cambio será persistido en la DB\n")

    async with Session() as db:
        await run_remap(db, dry_run)
        await run_seed_override(db, dry_run)

        if not dry_run:
            await report_coverage(db)
        else:
            print("\n[DRY-RUN] Reporte de cobertura omitido (cambios no aplicados)")

    await engine.dispose()


def main() -> None:
    """Entry point with CLI argument parsing."""
    parser = argparse.ArgumentParser(
        description="Clasificación masiva de property_type_normalized"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ejecutar sin persistir cambios en la DB",
    )
    args = parser.parse_args()

    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()

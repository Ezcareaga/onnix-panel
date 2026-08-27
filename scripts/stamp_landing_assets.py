#!/usr/bin/env python3
"""Sella el CSS y el JS de la landing con la versión del deploy.

`landing/index.html` pide sus assets con un `?v=` escrito a mano::

    <link rel="stylesheet" href="/landing-assets/css/styles.css?v=6">

nginx los sirve con `expires` de 30 días, así que mientras ese número no cambie
**el visitante que ya estuvo en el sitio sigue viendo el CSS viejo**. Un cambio
de diseño que nadie se acuerda de acompañar con un `?v=7` se despliega y no
llega: el 2026-08-20 el hero del celular quedó publicado y el navegador siguió
pintando el anterior.

Corre en el deploy de producción, después del checkout::

    python3 scripts/stamp_landing_assets.py --html /srv/onnix/prod/landing/index.html --version a1b2c3d

Es idempotente y no toca nada más: solo reemplaza el valor de `?v=` de los
`href`/`src` que apuntan a `/landing-assets/`. Si el archivo no existe o no hay
ninguno, sale 0 sin escribir — un deploy no se cae por esto.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Solo los assets propios de la landing, y solo su parámetro de versión. El
# grupo 1 se conserva tal cual: si el regex se comiera la ruta, la página
# quedaría sin estilos y eso es peor que un CSS viejo.
PATRON = re.compile(r'((?:href|src)="/landing-assets/[^"?]+)\?v=[^"]*"')


def sellar(html: str, version: str) -> tuple[str, int]:
    """Devuelve el HTML con `?v=<version>` y cuántos assets se sellaron."""
    nuevo, n = PATRON.subn(rf'\1?v={version}"', html)
    return nuevo, n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--html", required=True, type=Path)
    ap.add_argument("--version", required=True)
    args = ap.parse_args()

    if not args.html.is_file():
        print(f"sello: no existe {args.html} — no hago nada", file=sys.stderr)
        return 0

    original = args.html.read_text(encoding="utf-8")
    nuevo, n = sellar(original, args.version)
    if not n:
        print("sello: ningún asset de la landing tiene ?v= — no hago nada")
        return 0
    if nuevo != original:
        args.html.write_text(nuevo, encoding="utf-8")
    print(f"sello: {n} assets de la landing quedaron en ?v={args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

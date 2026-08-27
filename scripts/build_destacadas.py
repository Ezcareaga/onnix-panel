#!/usr/bin/env python3
"""Escribe el bloque de propiedades destacadas dentro de landing/index.html.

Decisión 1 de Ez (opción (b) del bloque 3 del LANDING_AUDIT): la landing sigue
siendo un archivo estático que nginx sirve del disco. Las destacadas se generan
**en el deploy**, no en cada request, así que la home sigue arriba aunque el
contenedor del panel esté caído. El precio es que las fotos quedan congeladas
hasta el próximo build.

Cómo corre (una sola vez, en el deploy de producción)::

    python3 scripts/build_destacadas.py \
        --html /srv/onnix/prod/landing/index.html \
        --database onnix_prod

Es idempotente: reemplaza lo que haya entre los dos marcadores HTML, así que
correrlo dos veces da el mismo archivo. Si la base no contesta o no hay ninguna
propiedad publicable, **no toca el archivo y sale 0**: el bloque que ya estaba
commiteado es un estado vacío válido con acción, y un deploy no se cae porque
las destacadas no se pudieron refrescar.

No importa nada de ``panel/``: corre en el host, fuera del contenedor, donde no
hay SQLAlchemy. Habla con Postgres por ``docker exec … psql`` y hace **una sola
consulta de lectura**.
"""
from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from pathlib import Path

INICIO = "<!-- destacadas:inicio — generado por scripts/build_destacadas.py -->"
FIN = "<!-- destacadas:fin -->"

# Etiquetas de tipo. Duplicadas a propósito de PORTAL_TIPO_OPTIONS
# (panel/app/services/public_property_service.py): importarlas arrastraría
# SQLAlchemy a un script que corre en el host. Que no se separen lo verifica
# test_las_etiquetas_de_tipo_no_se_separaron_del_portal.
ETIQUETAS_TIPO = {
    "casa": "Casa",
    "departamento": "Departamento",
    "departamento-en-pozo": "Departamento en pozo",
    "casa-en-condominio": "Casa en condominio",
    "casa-duplex": "Casa dúplex",
}

# Los mismos límites de plausibilidad que _PRICE_BOUNDS_USD para 'venta'. Fuera
# de esa franja el dato de origen está mal y el portal muestra "Consultar
# precio"; acá directamente no se destaca, porque una ficha sin precio no es
# una destacada.
PRECIO_MIN_USD = 1000
PRECIO_MAX_USD = 20_000_000

# El alias público de onnixpy. Duplicado a propósito: este script corre en el
# host, fuera del contenedor, y su docstring dice que no importa nada de
# `panel/`. Lo ata `panel/tests/test_alias_fotos.py`, que lee este archivo y el
# de la app y exige que digan lo mismo — si alguien cambia uno, se pone rojo.
ALIAS_Onnix = "p3"

# Una sola por barrio (o por ciudad si no tiene barrio): sin el DISTINCT ON, las
# seis destacadas salían siendo seis terrenos del mismo country club, que es lo
# que la base tiene cargado más recientemente.
CONSULTA = """
SELECT json_agg(fila ORDER BY fila.created_at DESC) FROM (
  SELECT DISTINCT ON (coalesce(neighborhood, city))
         id, external_id, city, neighborhood, property_type,
         bedrooms, bathrooms, total_area_m2, price_usd, created_at
  FROM properties
  WHERE source = 'onnixpy'
    AND is_active
    AND NOT on_hold
    AND duplicate_of IS NULL
    AND operation = 'venta'
    AND local_image_count > 0
    AND price_usd BETWEEN {minimo} AND {maximo}
    AND property_type IN ({tipos})
  ORDER BY coalesce(neighborhood, city), created_at DESC
) fila
"""


def consultar(database: str, contenedor: str, timeout: int) -> list[dict]:
    """Devuelve las candidatas de la base, o [] si algo no anduvo.

    Nunca levanta: el deploy no se cae porque las destacadas no se pudieron
    refrescar. El motivo sí va a stderr.
    """
    tipos = ", ".join(f"'{t}'" for t in sorted(ETIQUETAS_TIPO))
    sql = CONSULTA.format(minimo=PRECIO_MIN_USD, maximo=PRECIO_MAX_USD, tipos=tipos)
    cmd = [
        "docker", "exec", contenedor,
        "psql", "-U", "onnix", "-d", database, "-tAc", sql,
    ]
    try:
        salida = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"destacadas: no pude consultar la base ({exc}) — dejo el bloque como está",
              file=sys.stderr)
        return []
    if not salida:
        return []
    try:
        return json.loads(salida) or []
    except json.JSONDecodeError:
        print("destacadas: psql no devolvió JSON — dejo el bloque como está", file=sys.stderr)
        return []


def con_foto_en_disco(props: list[dict], images_dir: Path) -> list[dict]:
    """Filtra las que no tienen la foto en el disco.

    `local_image_count` es un contador de la base y ya divergió del disco una
    vez, cuando se perdieron las 9,7 GB de fotos. La landing es la página que
    más se comparte por WhatsApp: una caja de imagen rota ahí cuesta más que
    una destacada de menos.
    """
    return [
        p for p in props
        if (images_dir / "onnixpy" / str(p["external_id"]) / "1.webp").is_file()
    ]


def precio(valor) -> str:
    """`USD 120.000`. Punto de miles, que es el separador de Paraguay.

    Copia deliberada de `app.utils.money.precio`: este script corre en el host,
    sin SQLAlchemy. El carril J unificó el formato en `USD` y redondeó en vez de
    truncar; esta copia se quedó en `US$` y truncando, así que la landing
    escribía el precio distinto del portal que abre su propio link. Lo pinnea
    `test_el_precio_se_formatea_igual_que_en_el_portal`.
    """
    return f"USD {int(round(float(valor))):,}".replace(",", ".")


def _lugar(prop: dict) -> str:
    return prop.get("neighborhood") or prop.get("city") or "Paraguay"


def _tipo(prop: dict) -> str:
    bruto = prop.get("property_type") or ""
    return ETIQUETAS_TIPO.get(bruto, bruto.replace("-", " ").capitalize() or "Propiedad")


def _specs(prop: dict) -> list[str]:
    """Los datos que hay, en orden. Un campo vacío no deja un hueco."""
    partes = []
    if prop.get("bedrooms"):
        n = int(prop["bedrooms"])
        partes.append(f"{n} dormitorio" + ("s" if n != 1 else ""))
    if prop.get("bathrooms"):
        n = int(prop["bathrooms"])
        partes.append(f"{n} baño" + ("s" if n != 1 else ""))
    if prop.get("total_area_m2"):
        partes.append(f"{int(float(prop['total_area_m2']))} m²")
    return partes


def tarjeta(prop: dict) -> str:
    """Una ficha. El link va a /p/{id}, que redirige 301 al canónico.

    Así el slug lo arma el servidor una sola vez y este script no tiene una
    segunda copia de slugify() que se le desincronice.
    """
    tipo, lugar = _tipo(prop), _lugar(prop)
    etiqueta = f"{tipo} en {lugar} — {precio(prop['price_usd'])}"
    specs = _specs(prop)
    foto = f"/images/{ALIAS_Onnix}/{prop['external_id']}/1.webp"
    linea_specs = (
        f'\n          <p class="destacada-specs">{html.escape(" · ".join(specs))}</p>'
        if specs else ""
    )
    return f"""      <li class="destacada">
        <a href="/p/{int(prop['id'])}" aria-label="{html.escape(etiqueta)}">
          <img src="{html.escape(foto)}" alt="" width="800" height="600"
               loading="lazy" decoding="async">
          <p class="destacada-precio">{html.escape(precio(prop['price_usd']))}</p>
          <p class="destacada-tipo">{html.escape(tipo)} en {html.escape(lugar)}</p>{linea_specs}
        </a>
      </li>"""


def render(props: list[dict]) -> str:
    """El contenido entre los marcadores. Sin los marcadores."""
    fichas = "\n".join(tarjeta(p) for p in props)
    return f"""
    <ul class="destacadas-grid">
{fichas}
    </ul>
"""


def empalmar(documento: str, bloque: str) -> str:
    """Reemplaza lo que hay entre los marcadores. Los marcadores quedan."""
    i = documento.find(INICIO)
    f = documento.find(FIN)
    if i == -1 or f == -1 or f < i:
        raise ValueError(
            f"no encontré los marcadores de destacadas en el HTML "
            f"(inicio={i}, fin={f}). Sin ellos no sé dónde escribir."
        )
    return documento[: i + len(INICIO)] + bloque + documento[f:]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--html", required=True, type=Path,
                    help="index.html a reescribir (el del directorio de deploy)")
    ap.add_argument("--database", required=True,
                    help="base a consultar. Sin default a propósito: un default "
                         "que apunte a producción es como se rompe el aislamiento")
    ap.add_argument("--images-dir", type=Path, default=Path("/home/onnix/images"),
                    help="dónde están las fotos en el disco del VPS")
    ap.add_argument("--container", default="onnix-postgres")
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args(argv)

    documento = args.html.read_text(encoding="utf-8")
    # Falla ruidoso y antes de consultar la base: si el HTML no tiene los
    # marcadores es un bug del deploy, no un problema de datos.
    empalmar(documento, "")

    props = con_foto_en_disco(consultar(args.database, args.container, args.timeout),
                              args.images_dir)[: args.limit]
    if not props:
        print("destacadas: cero propiedades publicables — dejo el bloque como está",
              file=sys.stderr)
        return 0

    args.html.write_text(empalmar(documento, render(props)), encoding="utf-8")
    print(f"destacadas: {len(props)} fichas escritas en {args.html}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

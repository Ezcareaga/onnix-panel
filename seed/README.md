# Seed de catálogo de propiedades

El dump de propiedades **no está en el árbol de git** (pesa ~79 MB gzip por `raw_data`).
Va como **GitHub Release asset** para no inflar la historia del repo.

## Qué contiene
- `properties` — **28.750 filas** (19.457 activas + históricas), con `raw_data` (JSON crudo del scrape) y datos del catálogo.
- `property_types` — 11 filas de referencia.
- **Sin** la columna `description_embedding` (`vector(768)`): se regenera, no se versiona (ver abajo).

## Descargar
```bash
gh release download seed-v1 --repo Ezcareaga/Tasar-bd --pattern 'properties_seed.sql.gz'
gunzip properties_seed.sql.gz
```

## Restaurar (sobre una DB ya migrada)
```bash
# 1. Esquema primero
alembic upgrade head

# 2. Cargar el seed (como superusuario — usa SET session_replication_role para la FK circular de properties.duplicate_of)
psql -U <user> -d <db> -f properties_seed.sql
```

## Regenerar embeddings
El seed viene sin `description_embedding`. Para reconstruir la búsqueda semántica (pgvector HNSW):
corré el `embedding_updater` del scheduler (recalcula los vectores `vector(768)` desde `description`).
Hasta entonces la búsqueda vectorial devuelve vacío, pero la búsqueda SQL/textual funciona normal.

## Por qué no está en git
- Un blob de ~79 MB queda **permanente** en la historia (no se puede "desborrar" sin reescribirla).
- Los Release assets aceptan hasta 2 GB y mantienen el `git clone` liviano.

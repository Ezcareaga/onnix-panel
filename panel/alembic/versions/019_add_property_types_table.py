"""add property_types table and property_type_normalized column

Revision ID: 019
Revises: 018
Create Date: 2026-04-14

Fase 1 — Normalización de tipos de propiedad:
- Crea tabla property_types con los 11 códigos del catálogo canónico.
- Agrega columna property_type_normalized (FK nullable) en properties.
- Crea FK constraint y partial btree index sobre is_active activas.
- NO popular el campo todavía — eso es Fase 2 (clasificación masiva).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Crear tabla property_types
    op.create_table(
        "property_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("display_name", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "sort_order",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    # 2. Insertar los 11 tipos del catálogo canónico
    op.execute(
        text("""
        INSERT INTO property_types (id, code, display_name, description, sort_order) VALUES
        (1,  'CASA',         'Casa',         'Casa residencial, chalet, townhouse. Incluye casa en condominio/barrio cerrado. NO incluye duplex (2+ plantas con acceso independiente) ni quinta (terreno >1000m2 con amenidades rurales).', 1),
        (2,  'DEPARTAMENTO', 'Departamento', 'Departamento, monoambiente, estudio, loft, penthouse. Incluye "en pozo" y variantes (con jardin, con servicio de hotel). NO incluye duplex de 2 plantas ni edificio completo.', 2),
        (3,  'DUPLEX',       'Duplex',       'Vivienda de 2 o mas niveles con acceso independiente. Incluye triplex. Puede estar dentro de un edificio o ser unidad aislada.', 3),
        (4,  'TERRENO',      'Terreno',      'Lote/terreno urbano o suburbano sin construccion principal, o con construccion menor. Superficie tipica <5 hectareas. NO incluye campos rurales ni quintas con amenidades.', 4),
        (5,  'OFICINA',      'Oficina',      'Oficina comercial o corporativa. Incluye "oficinas" (plural) y pisos de oficinas.', 5),
        (6,  'LOCAL',        'Local',        'Local comercial, tienda, salon de ventas. NO incluye depositos ni naves industriales.', 6),
        (7,  'DEPOSITO',     'Deposito',     'Deposito, nave industrial, galpon, bodega, fabrica. Espacio de almacenamiento o produccion.', 7),
        (8,  'QUINTA',       'Quinta',       'Propiedad recreativa con terreno amplio (tipicamente >1000m2) y amenidades: piscina, quincho, jardin extenso, ambiente rural/country. Puede ser "casa quinta".', 8),
        (9,  'CAMPO',        'Campo',        'Propiedad rural/agricola/ganadera. Superficie tipicamente >5 hectareas. Incluye estancia, hacienda, propiedad agricola, livestock farm.', 9),
        (10, 'EDIFICIO',     'Edificio',     'Edificio completo en venta (no unidades individuales). Uso residencial, comercial o mixto.', 10),
        (99, 'OTRO',         'Otro',         'Estacionamiento, fraccionamiento, inmueble productivo, uso especial. No clasificable en las categorias anteriores.', 99)
        """)
    )

    # 3. Agregar columna property_type_normalized en properties (nullable, sin default)
    op.add_column(
        "properties",
        sa.Column("property_type_normalized", sa.Integer(), nullable=True),
    )

    # 4. Crear FK constraint
    op.create_foreign_key(
        "fk_properties_property_type_normalized",
        "properties",
        "property_types",
        ["property_type_normalized"],
        ["id"],
    )

    # 5. Crear partial btree index — solo propiedades activas y no duplicadas
    op.create_index(
        "idx_properties_type_normalized",
        "properties",
        ["property_type_normalized"],
        postgresql_where=text("is_active = true AND duplicate_of IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_properties_type_normalized", table_name="properties")
    op.drop_constraint(
        "fk_properties_property_type_normalized",
        "properties",
        type_="foreignkey",
    )
    op.drop_column("properties", "property_type_normalized")
    op.drop_table("property_types")

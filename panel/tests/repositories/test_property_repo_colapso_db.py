"""Integración — el colapso de proyectos del portal, contra la base de test.

El servicio del portal se testea con el repositorio mockeado, así que el SQL
—que es donde vive el colapso de verdad— no lo mira nadie desde ahí. Esto lo
ejecuta contra PostgreSQL real: `DISTINCT ON`, las dos funciones de ventana y
el `COUNT(DISTINCT (source, title))` que tiene que dar el mismo número.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.repositories.property_repo import PropertyRepository
from app.services.property_service import PropertyFilters


_TITULO = "PROYECTO DE PRUEBA COLAPSO — BOSQUE"
_FUENTE = "onnixpy"


@pytest.fixture
async def proyecto(db):
    """Tres unidades del mismo proyecto, con precios y fotos distintos."""
    await db.execute(
        text("DELETE FROM properties WHERE external_id LIKE 'colapso-%'")
    )
    # La MAS BARATA es la que NO tiene fotos, a propósito: si el precio y las
    # fotos coincidieran en la misma fila, sacar `local_image_count` del orden
    # del representante no cambiaría el resultado y el test no podría verlo.
    # (Pasó: la mutación sobrevivió hasta que se separaron los dos criterios.)
    filas = [
        # external_id, price_usd, area, fotos
        ("colapso-1", Decimal("21600"), Decimal("300"), 0),
        ("colapso-2", Decimal("30000"), Decimal("360"), 9),
        ("colapso-3", Decimal("50000"), Decimal("400"), 9),
    ]
    for ext, precio, area, fotos in filas:
        await db.execute(
            text(
                "INSERT INTO properties"
                " (source, external_id, title, city, operation, property_type,"
                "  price_usd, total_area_m2, local_image_count, is_active,"
                "  on_hold, created_at)"
                " VALUES (:s, :e, :t, 'Asuncion', 'venta', 'terreno',"
                "  :p, :a, :f, TRUE, FALSE, NOW())"
            ),
            {"s": _FUENTE, "e": ext, "t": _TITULO, "p": precio, "a": area,
             "f": fotos},
        )
    # Una cuarta con OTRO título, para que no se coma todo el grupo.
    await db.execute(
        text(
            "INSERT INTO properties"
            " (source, external_id, title, city, operation, property_type,"
            "  price_usd, total_area_m2, local_image_count, is_active,"
            "  on_hold, created_at)"
            " VALUES (:s, 'colapso-otro', :t, 'Asuncion', 'venta', 'terreno',"
            "  99000, 500, 3, TRUE, FALSE, NOW())"
        ),
        {"s": _FUENTE, "t": _TITULO + " II"},
    )
    await db.commit()
    yield
    await db.execute(
        text("DELETE FROM properties WHERE external_id LIKE 'colapso-%'")
    )
    await db.commit()


def _del_proyecto(filas):
    return [f for f in filas if f["title"] == _TITULO]


def _filtros():
    """Acotado a las filas del test.

    Sin `search_text` el `limit` tendría que cubrir el catálogo entero: el orden
    del listado manda las filas sin foto al final, y `colapso-1` —la que no
    tiene— quedaba fuera de las primeras 500.
    """
    return PropertyFilters(
        source=_FUENTE, state="active", search_text="COLAPSO"
    )


class TestColapsoDeProyectosSQL:
    async def test_sin_colapsar_salen_las_tres_unidades(self, db, proyecto):
        """El panel y el bot siguen viendo cada unidad."""
        filas = await PropertyRepository.list_with_filters(
            db, _filtros(), limit=500, offset=0,
        )
        assert len(_del_proyecto(filas)) == 3

    async def test_colapsando_sale_una_sola(self, db, proyecto):
        filas = await PropertyRepository.list_with_filters(
            db, _filtros(), limit=500, offset=0, colapsar_proyectos=True,
        )
        assert len(_del_proyecto(filas)) == 1

    async def test_la_fila_trae_el_conteo_y_el_minimo(self, db, proyecto):
        filas = await PropertyRepository.list_with_filters(
            db, _filtros(), limit=500, offset=0, colapsar_proyectos=True,
        )
        fila = _del_proyecto(filas)[0]
        assert fila["unidades"] == 3
        # El mínimo del grupo, no el precio de la fila elegida.
        assert Decimal(fila["precio_desde"]) == Decimal("21600")

    async def test_representa_al_grupo_la_que_tiene_fotos(self, db, proyecto):
        """La más barata (21.600) no tiene fotos: no puede representar.

        Una tarjeta sin foto es la que peor vende, y el portal la manda al final
        por algo. La que representa al proyecto es la más barata **de las que se
        pueden mostrar**: colapso-2, a 30.000.
        """
        filas = await PropertyRepository.list_with_filters(
            db, _filtros(), limit=500, offset=0, colapsar_proyectos=True,
        )
        fila = _del_proyecto(filas)[0]
        assert fila["local_image_count"] == 9
        assert fila["external_id"] == "colapso-2"

    async def test_el_conteo_colapsado_coincide_con_las_filas(self, db, proyecto):
        """Si el total no coincide con las tarjetas, la paginación miente."""
        filtros = _filtros()
        filas = await PropertyRepository.list_with_filters(
            db, filtros, limit=10_000, offset=0, colapsar_proyectos=True,
        )
        total = await PropertyRepository.count_with_filters(
            db, filtros, colapsar_proyectos=True
        )
        assert total == len(filas)

    async def test_el_conteo_sin_colapsar_sigue_siendo_el_de_antes(self, db, proyecto):
        filtros = _filtros()
        filas = await PropertyRepository.list_with_filters(
            db, filtros, limit=10_000, offset=0,
        )
        total = await PropertyRepository.count_with_filters(db, filtros)
        assert total == len(filas)

    async def test_colapsar_cuenta_menos_que_no_colapsar(self, db, proyecto):
        """El número que se ve en el encabezado del portal baja de verdad."""
        filtros = _filtros()
        sin = await PropertyRepository.count_with_filters(db, filtros)
        con = await PropertyRepository.count_with_filters(
            db, filtros, colapsar_proyectos=True
        )
        assert con < sin

    async def test_dos_titulos_distintos_no_se_colapsan_entre_si(self, db, proyecto):
        filas = await PropertyRepository.list_with_filters(
            db, _filtros(), limit=500, offset=0, colapsar_proyectos=True,
        )
        titulos = {f["title"] for f in filas if f["title"].startswith(_TITULO)}
        assert titulos == {_TITULO, _TITULO + " II"}

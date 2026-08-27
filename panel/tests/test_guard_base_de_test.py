"""La suite no puede correr contra una base que no sea la de test.

`conftest.py` setea POSTGRES_DB y dice «never production» en el docstring,
pero eso es una intencion, no una garantia: nada verifica contra que base
quedo conectado el engine. Una variable de entorno heredada, un engine que un
test arma por su cuenta o un `.env` distinto alcanzan para que la suite escriba
donde no debe — y la suite BORRA filas (`cleanup_test_data` corre TRUNCATE y
DELETE por patron al empezar y al terminar).

El guard pregunta `current_database()` por la conexion real y aborta si la
respuesta no esta en la allowlist. La diferencia con lo que ya habia es esa:
no chequea lo que declaramos, chequea donde estamos parados.
"""
from __future__ import annotations

import pytest

from tests._guards import BASES_PERMITIDAS, assert_base_de_test


class TestAllowlist:
    def test_acepta_la_base_de_desarrollo(self):
        assert_base_de_test("onnix_dev")

    def test_acepta_las_scratch_de_migraciones(self):
        """Las crea tests/migrations/conftest.py con el pid en el nombre."""
        assert_base_de_test("onnix_test_mig_31337")

    def test_acepta_la_base_por_worker_de_xdist(self):
        """Con `-n N` cada worker corre en la suya (ver TEST_DB en conftest).

        El nombre se eligió para entrar en el patrón que el guard YA aceptaba:
        habilitar xdist no aflojó el guard ni una línea.
        """
        assert_base_de_test("onnix_test_gw_0")
        assert_base_de_test("onnix_test_gw_11")

    @pytest.mark.parametrize(
        "base",
        [
            "onnix_prod",
            "postgres",
            "template1",
            "onnix",
            "",
        ],
    )
    def test_aborta_contra_cualquier_otra(self, base):
        with pytest.raises(RuntimeError, match="base de datos"):
            assert_base_de_test(base)

    def test_el_mensaje_nombra_la_base_encontrada(self):
        """Un guard que aborta sin decir donde estaba parado no sirve."""
        with pytest.raises(RuntimeError, match="onnix_prod"):
            assert_base_de_test("onnix_prod")

    def test_produccion_no_esta_en_la_allowlist(self):
        """Blindaje contra un futuro «agrego una base mas y de paso...»."""
        for permitida in BASES_PERMITIDAS:
            assert "prod" not in permitida


class TestBasePorWorker:
    """Con `-n N` cada worker escribe en su propia base, o no escribe nada.

    Sin esto, un futuro cambio que mande a todos los workers a la misma base
    no se ve: la suite entrega resultados sucios en silencio (67 fallos
    fantasma, ya pasó con dos sesiones sobre onnix_dev).
    """

    async def test_el_worker_corre_en_su_propia_base(self, db):
        import os

        import sqlalchemy

        worker = os.environ.get("PYTEST_XDIST_WORKER", "")
        if not worker:
            pytest.skip(
                "falta el worker de xdist: esto sólo se puede verificar con "
                "pytest -n N (sin -n no hay base por worker)"
            )
        actual = (
            await db.execute(sqlalchemy.text("SELECT current_database()"))
        ).scalar_one()
        assert actual != "onnix_dev", (
            f"el worker {worker} quedó en la base compartida — no hay aislamiento"
        )
        assert actual.endswith("_" + worker.removeprefix("gw")), (
            f"el worker {worker} corre en '{actual}', que no lleva su número"
        )


class TestGuardConectado:
    async def test_la_conexion_real_esta_en_la_base_de_test(self, db):
        """No la variable de entorno: la conexion.

        Este es el test que cierra el circulo — si el engine de la app termina
        en otra base, este assert lo dice aunque POSTGRES_DB diga lo correcto.
        """
        import sqlalchemy

        actual = (
            await db.execute(sqlalchemy.text("SELECT current_database()"))
        ).scalar_one()
        assert_base_de_test(actual)

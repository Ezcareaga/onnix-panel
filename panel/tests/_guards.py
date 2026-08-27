"""Guards que abortan la corrida antes de que toque datos que importan.

Pieza 0 de docs/METODOLOGIA-AGENTIC.md (proyecto oikoha): el entorno de tests
nunca debe tener credenciales de escritura sobre datos que importan, y hace
falta un guard que aborte si la base no es la de test.

Lo que este modulo NO resuelve, y queda anotado en TD-OPS-04: `onnix_dev`
no es efimera — es un snapshot de produccion y es la misma base que sirve
staging. El guard evita el desastre grande (correr contra `onnix_prod`),
no el chico.

Con `pytest -n N` eso deja de aplicar: cada worker corre en su propia
`onnix_test_gw_<n>`, efimera y construida por scripts/make_test_db.sh, y la
suite no toca `onnix_dev`. El patron de abajo ya las aceptaba sin cambios.
"""
from __future__ import annotations

import re

# La base de desarrollo, mas las scratch con pid que crea
# tests/migrations/conftest.py para no bloatear el attnum de contacts.
BASES_PERMITIDAS = frozenset({"onnix_dev"})
_SCRATCH = re.compile(r"^onnix_test_[a-z]+_\d+$")


def es_base_de_test(nombre: str) -> bool:
    return nombre in BASES_PERMITIDAS or bool(_SCRATCH.fullmatch(nombre))


def assert_base_de_test(nombre: str) -> None:
    """Aborta si `nombre` no es una base sobre la que la suite pueda escribir.

    El mensaje nombra la base encontrada: un guard que aborta sin decir donde
    estaba parado obliga a reproducir el problema para entenderlo.
    """
    if es_base_de_test(nombre):
        return
    raise RuntimeError(
        f"ABORTADO: la suite quedo conectada a la base de datos "
        f"'{nombre or '(vacia)'}', que no es una base de test. "
        f"Permitidas: {', '.join(sorted(BASES_PERMITIDAS))} y las scratch "
        f"onnix_test_<sufijo>_<pid>. "
        f"La suite BORRA filas al empezar y al terminar."
    )

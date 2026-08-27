"""Limpieza de los titulos que escribieron los portales de origen.

Los 19.972 titulos publicos son copy escrito a mano en otro sistema, y traen
tres cosas que rompen la ficha:

  - **62 usan glifos matematicos** (`U+1D400-1D7FF`: 𝐂𝐀𝐒𝐀 en vez de CASA).
    Estan fuera del `unicode-range` de los dos `@font-face` de Outfit, asi que
    el `h1` se dibuja con la fuente del sistema y un lector de pantalla los
    deletrea letra por letra.
  - **294 traen emoji**, que `ui.md` prohibe por nombre.
  - **107 dejan el slug vacio** porque el titulo se evapora al slugificar:
    `/prop/2750302-asuncion`, verificado por `curl`. Una URL sin titulo es una
    URL sin SEO.

Esto **no reescribe titulos**: saca caracteres. La diferencia importa — la
alternativa que se descarto era normalizar 19.923 filas de copy con logica de
verdad, con riesgo de destruir `mburucuyá` y `villa morra`. Aca las mayusculas,
el orden de las palabras y la puntuacion quedan intactos.

El trabajo pesado lo hace `unicodedata.normalize("NFKC", ...)`, que **convierte**
los glifos matematicos a su equivalente ASCII en vez de borrarlos: 𝐇𝐄𝐑𝐌𝐎𝐒𝐀 sale
como HERMOSA y no como nada. Solo se borra lo que no tiene equivalente.

**NFKC se aplica caracter por caracter y solo arriba de U+02FF**, que es donde
termina lo que la primera cara de Outfit dibuja. Aplicado a la cadena entera
normaliza de mas sobre cosas que la fuente SI puede dibujar: `100 m²` salia
`100 m2`, `Nº 5` salia `No 5` y `½` salia `1⁄2`. La regla es limpiar lo
ilegible, no reescribir lo que se lee bien.

Decision de Ez del 2026-08-23 (docs/audit/DECISIONES_PENDIENTES.md, la 8).
"""

import re
import unicodedata

# Categorias Unicode que la fuente no dibuja y que ningun titulo necesita:
# So = simbolos (emoji, flechas, ✅), Cf = formato invisible (ZWJ, marcas de
# direccion), Cc = control, Cs = surrogates sueltos, Co = uso privado,
# Cn = sin asignar.
_CATEGORIAS_FUERA = frozenset({"So", "Cf", "Cc", "Cs", "Co", "Cn"})

# Los selectores de variacion (U+FE00-FE0F) y el ZWJ son categoria Mn/Cf y
# viajan PEGADOS al emoji: sacar el emoji sin sacarlos deja un hueco invisible
# —`Duplex ️ Fernando`— que despues aparece como espacio doble.
_MODIFICADORES = re.compile(r"[︀-️‍⃣]")

# Lo que sobra en los bordes cuando se fue un emoji que abria o cerraba.
_BORDES = " \t-–—·|,;:.¡!¿?*+/\\"

# Hasta aca llega lo que la primera cara de Outfit dibuja (U+0000-00FF mas los
# modificadores hasta U+02FF). Debajo de este tope no hay nada que normalizar:
# `²`, `º` y `½` se quedan como estan.
_TOPE_DIBUJABLE = 0x02FF


def clean_title(text: str | None) -> str:
    """Devuelve el titulo sin lo que la fuente no puede dibujar.

    Args:
        text: El titulo crudo de `properties.title`, tal cual lo escribio el
            portal de origen.

    Returns:
        El mismo titulo sin emoji ni simbolos, con los glifos matematicos
        convertidos a ASCII y los espacios colapsados. Cadena vacia si no
        quedaba nada — el llamador decide el reemplazo, porque el de la card no
        es el del slug.
    """
    if not text:
        return ""
    limpio = "".join(
        unicodedata.normalize("NFKC", c) if ord(c) > _TOPE_DIBUJABLE else c
        for c in text
    )
    limpio = _MODIFICADORES.sub("", limpio)
    limpio = "".join(
        c for c in limpio if unicodedata.category(c) not in _CATEGORIAS_FUERA
    )
    return re.sub(r"\s+", " ", limpio).strip(_BORDES)

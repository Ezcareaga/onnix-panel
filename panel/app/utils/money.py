"""Un solo formato de número para todo lo que muestra plata o cantidades.

En Paraguay el separador de miles es el punto. Antes de esto convivían tres
formatos en la misma pantalla de propiedades — ``1.234`` en el contador,
``USD 250,000`` en la celda de precio y en el texto que el asesor copia al
portapapeles, y ``US$ 250.000`` en la página pública que ese mismo link abre.
El asesor mandaba por WhatsApp un precio con coma y el cliente abría una página
con punto.

Todo lo que renderiza un número pasa por acá, y los templates lo usan como
globals de Jinja (``miles`` y ``precio``, registrados en ``app.tz``).
"""

from __future__ import annotations

MONEDA_USD = "USD"
MONEDA_PYG = "₲"


def miles(n) -> str:
    """``250000`` → ``'250.000'``. Lo que no es número vuelve tal cual."""
    try:
        return f"{int(round(float(n))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(n)


def precio(price_usd=None, price_pyg=None, vacio: str = "A consultar") -> str:
    """Precio listo para mostrar: USD primero, ₲ de fallback, si no ``vacio``.

    Un precio en 0 o None no es un precio: cae en ``vacio``, nunca en ``USD 0``.
    """
    if price_usd:
        return f"{MONEDA_USD} {miles(price_usd)}"
    if price_pyg:
        return f"{MONEDA_PYG} {miles(price_pyg)}"
    return vacio

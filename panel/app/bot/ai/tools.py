"""Tool definitions for Claude tool-use in the Onnix SA bot.

These tools are sent to Claude's Messages API so it can invoke
structured actions (search, detail, lead registration) on behalf
of the user.
"""
from __future__ import annotations

import copy


# M6.3 Plan 123-03 (BOT-05/BOT-13 + D-1): schedule a visit when the client
# confirmed a concrete day/time. Module-level so 123-02 can include it in
# get_tools(mode) for the recepcionista mode. NO contact_id field — Claude
# never sees ids; the handler injects contact_id from search_context.
AGENDAR_VISITA_TOOL: dict = {
    "name": "agendar_visita",
    "description": (
        "Agenda una visita a una propiedad para un cliente que ya mostró "
        "interés concreto y propuso o aceptó una fecha/hora. Usar SOLO cuando "
        "el cliente confirmó día y horario. El asesor humano confirmará luego."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scheduled_at_iso": {
                "type": "string",
                "description": (
                    "Fecha y hora de la visita en ISO 8601 con zona horaria "
                    "(ej: '2026-06-10T15:00:00-03:00'). Debe ser futura."
                ),
            },
            "property_id": {
                "type": "integer",
                "description": "ID de la propiedad a visitar, si se conoce. Opcional.",
            },
        },
        "required": ["scheduled_at_iso"],
    },
}


TOOLS: list[dict] = [
    {
        "name": "search_properties",
        "description": (
            "Busca propiedades inmobiliarias en la base de datos "
            "de Onnix SA Paraguay"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operacion": {
                    "type": "string",
                    "enum": ["venta", "alquiler"],
                    "description": (
                        "Tipo de operacion. 'En pozo', 'en construccion' "
                        "o 'sobre plano' siempre es venta."
                    ),
                },
                "tipo": {
                    "type": "string",
                    "enum": [
                        "casa", "departamento", "duplex", "terreno",
                        "oficina", "local", "deposito", "quinta",
                        "campo", "edificio", "otro",
                    ],
                    "description": (
                        "Tipo de propiedad normalizado. "
                        "'ph' y 'duplex' -> duplex. "
                        "'nave', 'galpon', 'bodega' -> deposito. "
                        "'en pozo' -> departamento con descripcion_libre='en pozo'. "
                        "'estancia', 'hacienda' -> campo."
                    ),
                },
                "ciudad": {
                    "type": "string",
                    "description": "Ciudad principal donde buscar",
                },
                "barrio": {
                    "type": "string",
                    "description": "Barrio o zona dentro de la ciudad",
                },
                "barrios": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Lista de barrios si el usuario menciona varios"
                    ),
                },
                "precio_min": {
                    "type": "number",
                    "description": "Precio minimo en la moneda indicada",
                },
                "precio_max": {
                    "type": "number",
                    "description": "Precio maximo en la moneda indicada",
                },
                "dormitorios_min": {
                    "type": "integer",
                    "description": (
                        "Cantidad minima de dormitorios. Usar cuando el usuario dice "
                        "'minimo X', 'al menos X', 'X o mas dormitorios'. "
                        "Para 'X dormitorios' exacto (sin contexto de minimo o maximo), "
                        "usar dormitorios_min=X junto con dormitorios_max=X."
                    ),
                },
                "dormitorios_max": {
                    "type": "integer",
                    "description": (
                        "Cantidad maxima de dormitorios. Usar cuando el usuario dice "
                        "'maximo X', 'hasta X', 'no mas de X dormitorios'. "
                        "NUNCA usar dormitorios_min cuando el usuario quiere un maximo."
                    ),
                },
                "bathrooms_min": {
                    "type": "integer",
                    "description": (
                        "Cantidad minima de banos. Usar cuando el usuario dice "
                        "'minimo X banos', 'al menos X banos'."
                    ),
                },
                "bathrooms_max": {
                    "type": "integer",
                    "description": (
                        "Cantidad maxima de banos. Usar cuando el usuario dice "
                        "'maximo X banos', 'hasta X banos'."
                    ),
                },
                "area_min": {
                    "type": "number",
                    "description": (
                        "Superficie minima en metros cuadrados. "
                        "Usar cuando el usuario dice 'minimo X m2', 'al menos X metros'."
                    ),
                },
                "area_max": {
                    "type": "number",
                    "description": (
                        "Superficie maxima en metros cuadrados. "
                        "Usar cuando el usuario dice 'maximo X m2', 'hasta X metros cuadrados'."
                    ),
                },
                "moneda": {
                    "type": "string",
                    "enum": ["usd", "gs"],
                    "default": "usd",
                    "description": "Moneda del rango de precios",
                },
                "descripcion_libre": {
                    "type": "string",
                    "description": (
                        "Texto libre adicional para busqueda semantica"
                    ),
                },
                "estado_construccion": {
                    "type": "string",
                    "enum": ["en_pozo", "en_construccion", "a_estrenar", "terminado"],
                    "description": (
                        "Estado de construcción de la propiedad: "
                        "en_pozo (preventa), en_construccion (en obras), "
                        "a_estrenar (recién terminado, sin uso), "
                        "terminado (habitado o usado)."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_property_detail",
        "description": (
            "Obtiene los detalles completos de una propiedad especifica"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "referencia": {
                    "type": "string",
                    "description": (
                        "Referencia a la propiedad: puede ser el numero "
                        "ordinal ('la primera', 'la segunda', 'la 3') "
                        "o el ID de propiedad"
                    ),
                },
            },
            "required": ["referencia"],
        },
    },
    {
        "name": "register_lead",
        "description": (
            "Registra el lead para que un asesor humano de Onnix SA lo "
            "atienda. Usala tanto cuando el cliente pide hablar con un asesor "
            "como cuando evade dar el nombre o solo da criterios de búsqueda: en "
            "ese caso derivá igual con captura parcial (sin nombre). NO requiere "
            "nombre para llamarse."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "motivo": {
                    "type": "string",
                    "description": (
                        "Motivo por el cual el usuario quiere hablar "
                        "con un asesor"
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "process_opt_out",
        "description": (
            "Procesa la solicitud del usuario de dejar de recibir mensajes del bot. "
            "Usar cuando el usuario dice que quiere darse de baja, no quiere mas mensajes, "
            "o pide que dejen de escribirle."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "resolver_zona",
        "description": (
            "Resuelve texto ambiguo de zona (\"por san ber\", \"cerca del centro\", "
            "\"por lado de Villa Morra\") a ciudad/barrio/landmark canonical. "
            "Úsala cuando el cliente menciona una zona que NO está en "
            "{Asunción, Luque, Encarnación, San Bernardino, San Lorenzo}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "texto": {
                    "type": "string",
                    "description": (
                        "Texto libre del cliente con la zona — puede incluir "
                        "landmark, alias, abreviación."
                    ),
                },
            },
            "required": ["texto"],
        },
    },
    # NOTE: AGENDAR_VISITA_TOOL is intentionally NOT in this default list.
    # It is a module-level dict (defined above) that Plan 123-02 wires into
    # get_tools(mode) for the recepcionista mode only. Adding it here would
    # expose it to every mode prematurely.
]


# M6.3 Plan 123-02 (BOT-03/BOT-04): tools exposed in recepcionista mode.
# search_properties is intentionally absent — the recepcionista bot qualifies
# leads, it does not run searches. agendar_visita is recepcionista-specific.
_RECEPCIONISTA_TOOL_NAMES = {
    "get_property_detail",
    "register_lead",
    "process_opt_out",
    "resolver_zona",
    "agendar_visita",
}


def get_tools(mode: str = "busqueda") -> list[dict]:
    """Return a deep copy of the tool definitions for the given mode.

    A copy is returned so callers cannot accidentally mutate the
    module-level ``TOOLS`` list or ``AGENDAR_VISITA_TOOL`` dict.

    - ``busqueda`` (default): all 6 tools (the 5 originals + agendar_visita).
    - ``recepcionista``: excludes search_properties, keeps agendar_visita.

    The 5 originals in ``TOOLS`` are never deleted — only filtered out.
    """
    pool = copy.deepcopy(TOOLS + [AGENDAR_VISITA_TOOL])
    if mode == "recepcionista":
        return [t for t in pool if t["name"] in _RECEPCIONISTA_TOOL_NAMES]
    return pool

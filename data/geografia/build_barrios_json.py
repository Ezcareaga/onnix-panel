#!/usr/bin/env python3
"""Build barrios_asuncion_vecinos.json from scraped Wikipedia data."""
import json
import unicodedata
from datetime import date

def normalize_key(name):
    """Remove accents and lowercase for JSON key."""
    nfkd = unicodedata.normalize('NFKD', name.lower())
    return ''.join(c for c in nfkd if not unicodedata.combining(c))

# Alias map: alternative names -> canonical display name
ALIASES = {
    "bernardino caballero": "General Caballero",
    "general caballero": "General Caballero",
    "silvio pettirossi": "Pettirossi",
    "pettirossi": "Pettirossi",
    "roberto luis petit": "Roberto L. Pettit",
    "roberto l. pettit": "Roberto L. Pettit",
    "obrero": "Barrio Obrero",
    "barrio obrero": "Barrio Obrero",
    "chacarita": "Ricardo Brugada",
    "la chacarita": "Ricardo Brugada",
    "catedral": "La Catedral",
    "la catedral": "La Catedral",
    "encarnacion": "La Encarnación",
    "la encarnacion": "La Encarnación",
    "cañado del ybyray": "Cañada del Ybyray",  # typo in one article
    "hipodromo": "Barrio Hipódromo",
    "barrio hipodromo": "Barrio Hipódromo",
    "gral. diaz": "General Díaz",
    "de la residenta": None,  # Not in our list, ignore
    "bañado": None,  # Ambiguous, ignore
    "cateura": None,  # Not a standard barrio name
}

def resolve_name(raw):
    """Resolve a raw neighbor name to canonical display name."""
    lower = raw.strip().lower()
    if lower in ALIASES:
        return ALIASES[lower]
    # Try without accents
    norm = normalize_key(raw.strip())
    if norm in {normalize_key(v) for v in ALIASES.values() if v}:
        for v in ALIASES.values():
            if v and normalize_key(v) == norm:
                return v
    return raw.strip()

# All barrios with their explicit neighbors from Wikipedia
# Format: "Display Name": [list of neighbor display names]
RAW_DATA = {
    "Banco San Miguel": ["Tablada Nueva", "San Juan", "Las Mercedes"],
    "Bañado Cará Cará": ["Botánico", "Santa Rosa", "Virgen de Fátima", "Tablada Nueva"],
    "Barrio Obrero": [],  # sin datos explícitos
    "Bella Vista": ["Virgen de la Asunción", "Cañada del Ybyray", "Santo Domingo", "Villa Morra", "Recoleta", "Mariscal López", "Virgen del Huerto"],
    "Botánico": ["Zeballos Cué", "Loma Pytá", "Santísima Trinidad", "Santa Rosa", "Bañado Cará Cará"],
    "Cañada del Ybyray": ["Santísima Trinidad", "Mburucuyá", "Las Lomas", "Santo Domingo", "Bella Vista", "Virgen de la Asunción"],
    "Ciudad Nueva": ["San José", "Las Mercedes", "Mariscal López", "General Caballero", "Pinozá", "Pettirossi", "San Roque"],
    "Dr. Francia": [],  # solo menciona río Paraguay
    "La Encarnación": [],  # sin datos explícitos
    "General Caballero": ["Mariscal López", "Mburicaó", "Vista Alegre", "Pinozá", "Ciudad Nueva", "Las Mercedes"],
    "General Díaz": ["La Catedral", "San Roque", "Barrio Obrero", "Pettirossi", "Tacumbú", "La Encarnación"],
    "Herrera": ["Ycuá Satí", "San Jorge", "Ytay", "Santa María", "Villa Aurelia", "Mariscal Estigarribia", "San Cristóbal"],
    "Barrio Hipódromo": ["Tembetary", "Los Laureles", "Villa Aurelia", "San Pablo", "Terminal", "Nazareth"],
    "Itá Enramada": ["Republicano"],
    "Itá Pytã Punta": [],  # solo menciona calles
    "Jara": ["San Juan", "Virgen de la Asunción", "Virgen del Huerto", "Mariscal López", "Las Mercedes"],
    "Jukyty": [],  # sin datos
    "Carlos A. López": ["Tacumbú", "Sajonia", "Dr. Francia"],
    "La Catedral": ["La Encarnación", "General Díaz", "San Roque", "Ricardo Brugada"],
    "Las Lomas": ["Mburucuyá", "Madame Lynch", "San Jorge", "Manorá", "Santo Domingo", "Cañada del Ybyray"],
    "Loma Pytá": ["Zeballos Cué", "San Blas", "Ñu Guazú", "Mbocayaty", "Botánico"],
    "Los Laureles": ["Recoleta", "Mariscal Estigarribia", "Villa Aurelia", "San Pablo", "Barrio Hipódromo", "Nazareth", "Tembetary"],
    "Madame Lynch": ["Mbocayaty", "Salvador del Mundo", "San Jorge", "Las Lomas", "Mburucuyá"],
    "Manorá": ["Las Lomas", "Ycuá Satí", "Villa Morra", "Santo Domingo"],
    "Mariscal Estigarribia": ["Villa Morra", "San Cristóbal", "Herrera", "Villa Aurelia", "Los Laureles", "Recoleta"],
    "Mariscal López": ["Jara", "Virgen del Huerto", "Bella Vista", "Recoleta", "Mburicaó", "General Caballero", "Las Mercedes"],
    "Mbocayaty": ["Loma Pytá", "Ñu Guazú", "Salvador del Mundo", "Madame Lynch", "Mburucuyá", "Santísima Trinidad"],
    "Mburicaó": ["Mariscal López", "Recoleta", "Tembetary", "Nazareth", "Vista Alegre", "General Caballero"],
    "Mburucuyá": ["Mbocayaty", "Madame Lynch", "Las Lomas", "Cañada del Ybyray", "Santísima Trinidad"],
    "Las Mercedes": ["Banco San Miguel", "San Juan", "Jara", "Mariscal López", "General Caballero", "Ciudad Nueva", "San Roque", "San José", "Ricardo Brugada"],
    "Nazareth": ["Mburicaó", "Tembetary", "Los Laureles", "Barrio Hipódromo", "Terminal", "Vista Alegre"],
    "Ñu Guazú": ["Loma Pytá", "Ytay", "Salvador del Mundo", "Mbocayaty"],
    "Pettirossi": [],  # parcial - solo calles
    "Pinozá": ["Ciudad Nueva", "General Caballero", "Vista Alegre", "San Vicente", "Pettirossi"],
    "Roberto L. Pettit": [],  # sin datos
    "Recoleta": ["Bella Vista", "Santo Domingo", "Villa Morra", "San Cristóbal", "Mariscal Estigarribia", "Los Laureles", "Tembetary", "Mburicaó", "Mariscal López"],
    "Republicano": ["Roberto L. Pettit", "Santa Ana", "San Vicente", "Itá Enramada"],
    "Ricardo Brugada": ["Las Mercedes", "San José", "San Roque", "La Catedral", "La Encarnación"],
    "Sajonia": ["Tacumbú", "Carlos A. López"],
    "Salvador del Mundo": ["Mbocayaty", "Ñu Guazú", "San Jorge", "Madame Lynch"],
    "San Antonio": ["Carlos A. López", "Dr. Francia", "Itá Pytã Punta"],
    "San Blas": ["Loma Pytá"],
    "San Cayetano": [],  # sin datos
    "San Cristóbal": ["Ycuá Satí", "Herrera", "Mariscal Estigarribia", "Recoleta", "Villa Morra"],
    "San Jorge": ["Salvador del Mundo", "Ñu Guazú", "Ytay", "Santa María", "Herrera", "Ycuá Satí", "Manorá", "Las Lomas", "Madame Lynch"],
    "San José": ["Ricardo Brugada", "Las Mercedes", "Ciudad Nueva", "San Roque"],
    "San Juan": ["Banco San Miguel", "Tablada Nueva", "Virgen de la Asunción", "Jara", "Las Mercedes"],
    "San Pablo": ["Los Laureles", "Villa Aurelia", "Barrio Hipódromo", "Terminal"],
    "San Roque": ["Ricardo Brugada", "Pettirossi", "General Díaz", "Las Mercedes", "Ciudad Nueva", "La Catedral"],
    "San Vicente": ["Pettirossi", "Pinozá", "Santa Librada", "Republicano", "Roberto L. Pettit", "Barrio Obrero"],
    "Santa Ana": ["Republicano", "Itá Enramada"],
    "Santa Librada": [],  # solo dice "limitante con Lambaré"
    "Santa María": ["Ycuá Satí", "San Jorge", "Ytay", "Villa Aurelia", "Herrera"],
    "Santa Rosa": ["Botánico", "Santísima Trinidad", "Virgen de la Asunción", "Virgen de Fátima", "Bañado Cará Cará"],
    "Santísima Trinidad": ["Botánico", "Mbocayaty", "Mburucuyá", "Cañada del Ybyray", "Virgen de la Asunción", "Virgen de Fátima", "Santa Rosa"],
    "Santo Domingo": ["Cañada del Ybyray", "Las Lomas", "Manorá", "Villa Morra", "Recoleta", "Bella Vista"],
    "Tablada Nueva": ["Bañado Cará Cará", "Virgen de Fátima", "Virgen de la Asunción", "San Juan", "Banco San Miguel"],
    "Tacumbú": ["Sajonia", "Carlos A. López", "General Díaz"],
    "Tembetary": ["Recoleta", "Los Laureles", "Barrio Hipódromo", "Nazareth", "Vista Alegre", "Mburicaó"],
    "Terminal": ["Barrio Hipódromo", "San Pablo", "Nazareth"],
    "Villa Aurelia": ["Herrera", "Santa María", "San Pablo", "Los Laureles", "Mariscal Estigarribia"],
    "Villa Morra": ["Santo Domingo", "Manorá", "Ycuá Satí", "San Cristóbal", "Mariscal Estigarribia", "Recoleta", "Bella Vista"],
    "Virgen de Fátima": ["Santa Rosa", "Virgen de la Asunción", "Tablada Nueva", "Bañado Cará Cará", "Santísima Trinidad"],
    "Virgen de la Asunción": ["Santa Rosa", "Santísima Trinidad", "Cañada del Ybyray", "Bella Vista", "Virgen del Huerto", "Jara", "San Juan", "Tablada Nueva", "Virgen de Fátima"],
    "Virgen del Huerto": ["Virgen de la Asunción", "Bella Vista", "Mariscal López", "Jara"],
    "Vista Alegre": ["Mburicaó", "Tembetary", "Nazareth", "Pinozá", "General Caballero"],
    "Ycuá Satí": ["San Jorge", "Ytay", "Santa María", "Herrera", "San Cristóbal", "Villa Morra", "Manorá"],
    "Ytay": ["Ñu Guazú", "Santa María", "Herrera", "Ycuá Satí", "San Jorge", "Salvador del Mundo"],
    "Zeballos Cué": ["Loma Pytá", "Botánico"],
}

# Build the adjacency map
barrios_map = {}
for barrio, vecinos in RAW_DATA.items():
    key = normalize_key(barrio)
    barrios_map[key] = {
        "display": barrio,
        "vecinos": [normalize_key(resolve_name(v)) for v in vecinos if resolve_name(v)],
        "fuente": "explicita" if vecinos else "sin_datos"
    }

# Enforce bidirectionality
additions = []
for barrio_key, data in barrios_map.items():
    for vecino_key in data["vecinos"]:
        if vecino_key in barrios_map:
            if barrio_key not in barrios_map[vecino_key]["vecinos"]:
                additions.append((vecino_key, barrio_key))

for vecino_key, barrio_key in additions:
    barrios_map[vecino_key]["vecinos"].append(barrio_key)
    if barrios_map[vecino_key]["fuente"] == "sin_datos":
        barrios_map[vecino_key]["fuente"] = "inferida_cruzada"

# Remove duplicates
for key in barrios_map:
    barrios_map[key]["vecinos"] = sorted(set(barrios_map[key]["vecinos"]))

# Verify bidirectionality
issues = []
for barrio_key, data in barrios_map.items():
    for vecino_key in data["vecinos"]:
        if vecino_key not in barrios_map:
            issues.append(f"ERROR: {barrio_key} -> {vecino_key} NO EXISTE")
        elif barrio_key not in barrios_map[vecino_key]["vecinos"]:
            issues.append(f"WARN: {barrio_key} -> {vecino_key} NO RECIPROCO")

# Build output
output = {
    "metadata": {
        "fuente": "Wikipedia ES",
        "fecha_extraccion": str(date.today()),
        "tipo": "barrios_asuncion",
        "notas": "Relaciones bidireccionales verificadas. Barrios sin datos = no tenían sección de límites en Wikipedia."
    },
    "barrios": barrios_map
}

with open("/home/ez/projects/onnix-bot/data/geografia/barrios_asuncion_vecinos.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

# Stats
total = len(barrios_map)
con_vecinos = sum(1 for v in barrios_map.values() if v["vecinos"])
sin_datos = sum(1 for v in barrios_map.values() if v["fuente"] == "sin_datos")
explicitas = sum(1 for v in barrios_map.values() if v["fuente"] == "explicita")
inferidas = sum(1 for v in barrios_map.values() if v["fuente"] == "inferida_cruzada")
total_relaciones = sum(len(v["vecinos"]) for v in barrios_map.values()) // 2

print("=== RESULTADOS ===")
if issues:
    for i in issues:
        print(i)
else:
    print("BIDIRECCIONALIDAD VERIFICADA")

print(f"\nTotal barrios: {total}")
print(f"Con vecinos explícitos: {explicitas}")
print(f"Con vecinos inferidos: {inferidas}")
print(f"Sin datos de límites: {sin_datos}")
print(f"Total relaciones bidireccionales: {total_relaciones}")

# List barrios sin datos
print(f"\nBarrios sin datos:")
for k, v in barrios_map.items():
    if v["fuente"] == "sin_datos":
        print(f"  - {v['display']}")

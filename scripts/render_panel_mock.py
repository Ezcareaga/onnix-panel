#!/usr/bin/env python3
"""Renderiza las pantallas del panel con datos inventados, sin tocar la base.

**Por qué existe.** Para grabar tutoriales del panel hace falta una captura, y
hoy la única forma de tener una es abrir staging — que `scripts/refresh_dev_from_prod.sh`
restaura desde producción **sin anonimizar**. Una captura de staging publica
nombres, teléfonos y conversaciones de clientes reales. Este script rompe esa
dependencia: el HTML sale de los mismos templates y del mismo CSS que sirve el
contenedor, pero los datos son inventados y viven acá abajo, entre «Datos
inventados» y `# MOCK-FIN`.

**No se conecta a Postgres.** No importa `app.database`, no abre una sesión y no
lee un `.env`. Lo único que toma del código de la app es el entorno de Jinja
(`app.tz.get_templates`), para que los filtros y globals sean los mismos que en
producción.

**De dónde sale el CSS.** El `tailwind.css` commiteado NO es el que sirve la
app: el Dockerfile lo compila en el build (`panel/Dockerfile:44-46`), así que
las clases arbitrarias que el repo tiene sin recompilar faltan en el archivo del
árbol. Por eso, por defecto, el CSS se saca de adentro del contenedor con
`docker exec onnix-panel cat app/static/css/tailwind.css`. Si no hay docker —o el
contenedor no está—, cae al archivo del repo y **lo dice en stderr**: esa salida
puede no coincidir con lo que ve la administradora. `--css-from` elige el contenedor.

**Es determinista.** Dos corridas producen bytes idénticos: no hay `random`, no
hay `datetime.now()` y los dos filtros que dependen del reloj —`humandate` y
`wa_timestamp`— se fijan a `NOW`. El consumidor es un pipeline de video: un
pixel que cambia solo es un video que cambia solo.

**Dónde corre.** En el VPS, con el venv: `/home/onnix/.venv/bin/python`
(Python 3.12). En la laptop hay un 3.9 pelado sin jinja2.

Uso:
    /home/onnix/.venv/bin/python scripts/render_panel_mock.py
    /home/onnix/.venv/bin/python scripts/render_panel_mock.py --out /tmp/panel-mock

Cada pantalla queda en `<out>/<seccion>[-<vista>].html`, con `<out>/static/` al
lado y **cero rutas absolutas adentro**: el directorio se puede mover entero y
sigue abriendo con `file://`. `<out>/manifest.json` es el índice que lee el
capturador; `<out>/index.html` es el mismo índice para un humano.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
OUT_DEFAULT = ROOT / "tutoriales" / "capturas" / "html"
PANEL = ROOT / "panel"
TEMPLATES = PANEL / "app" / "templates"
STATIC = PANEL / "app" / "static"

if str(PANEL) not in sys.path:
    sys.path.insert(0, str(PANEL))

from app.constants import BADGE_MAP  # noqa: E402
from app.tz import PYT, humandate, wa_timestamp  # noqa: E402

# El reloj congelado. Todas las fechas inventadas cuelgan de acá.
NOW = datetime(2026, 8, 20, 15, 42, tzinfo=PYT)


def _h(horas: float) -> datetime:
    """Un instante `horas` antes de NOW."""
    return NOW - timedelta(hours=horas)


def _d(dias: float) -> datetime:
    """Un instante `dias` antes de NOW."""
    return NOW - timedelta(days=dias)


# --------------------------------------------------------------------------
# Datos inventados
# --------------------------------------------------------------------------
# Nombres, barrios y ciudades son paraguayos y reales; las personas no existen.
#
# **Los teléfonos son del bloque reservado de este archivo**: `+595 9XX 000 0NN`.
# Ningún número asignado en Paraguay tiene ese cuerpo, así que un teléfono con
# formato de real —`+595 981 234 567`— se distingue mecánicamente de uno
# inventado. Eso es lo que verifica `panel/tests/test_render_panel_mock.py`, y
# es la única barrera que hay entre este script y publicar PII: un renderizador
# que puede filtrar un dato de producción sin avisar es peor que no tenerlo.
# Los correos van al TLD `.test`, reservado por la RFC 6761.

TEL = {  # bloque reservado: +595 9XX 000 0NN
    "rocio": "+595 981 000 001",
    "gustavo": "+595 971 000 002",
    "larissa": "+595 985 000 003",
    "mariela": "+595 991 000 011",
    "derlis": "+595 972 000 012",
    "nadia": "+595 983 000 013",
    "cristian": "+595 976 000 014",
    "vanessa": "+595 992 000 015",
    "alcides": "+595 984 000 016",
}

ADMIN = {
    "id": 1,
    "email": "rocio.duarte@onnix.test",
    "name": "Rocío Duarte",
    "display_name": "Rocío Duarte",
    "phone": TEL["rocio"],
    "role": "admin",
    "is_active": True,
    "created_at": _d(420),
}

ASESORES = [
    {"id": 2, "email": "gustavo.ramirez@onnix.test", "name": "Gustavo Ramírez",
     "display_name": "Gustavo Ramírez", "phone": TEL["gustavo"], "role": "agent",
     "is_active": True, "created_at": _d(300)},
    {"id": 3, "email": "larissa.ojeda@onnix.test", "name": "Larissa Ojeda",
     "display_name": "Larissa Ojeda", "phone": TEL["larissa"], "role": "agent",
     "is_active": True, "created_at": _d(120)},
    {"id": 4, "email": "sofia.benitez@onnix.test", "name": "Sofía Benítez",
     "display_name": "Sofía Benítez", "phone": None, "role": "agent",
     "is_active": False, "created_at": _d(200)},
]

# --- dashboard -------------------------------------------------------------
DASHBOARD_STATS = {
    "status_counts": {
        "new": 34, "bot_replied": 21, "agent_replied": 17, "interested": 12,
        "visit_scheduled": 6, "closed": 4, "no_response": 58, "discarded": 9,
    },
    "total_leads": 161,
    "bot_enabled": True,
    "new_today": 7,
    "messages_24h": 128,
    "errors_24h": 0,
    "lead_tab_counts": {
        "leads": 161, "interesados": 12, "asignados": 43, "sin_respuesta": 58,
    },
}

DEMAND_30 = {
    "days": 30,
    "total": 214,
    "by_source": {"infocasas": 151, "whatsapp": 49, "telegram": 14},
    "top_cities": [
        {"label": "Asunción", "count": 78, "pct": 36},
        {"label": "Luque", "count": 41, "pct": 19},
        {"label": "San Lorenzo", "count": 33, "pct": 15},
        {"label": "Fernando de la Mora", "count": 26, "pct": 12},
        {"label": "Ciudad del Este", "count": 18, "pct": 8},
    ],
    "top_types": [
        {"label": "departamento", "count": 92, "pct": 43},
        {"label": "casa", "count": 71, "pct": 33},
        {"label": "duplex", "count": 24, "pct": 11},
        {"label": "terreno", "count": 17, "pct": 8},
        {"label": "local", "count": 10, "pct": 5},
    ],
    "operations": {"venta": 137, "alquiler": 77},
    "sin_ciudad": 18,
    "monthly": [
        {"label": "Mar", "count": 122, "pct": 47},
        {"label": "Abr", "count": 158, "pct": 61},
        {"label": "May", "count": 190, "pct": 73},
        {"label": "Jun", "count": 171, "pct": 66},
        {"label": "Jul", "count": 259, "pct": 100},
        {"label": "Ago", "count": 214, "pct": 83},
    ],
}

DEMAND_90 = dict(DEMAND_30, days=90, total=612,
                 by_source={"infocasas": 438, "whatsapp": 132, "telegram": 42},
                 operations={"venta": 401, "alquiler": 211}, sin_ciudad=53)


# --- stats -----------------------------------------------------------------
def _serie(counts: tuple[int, ...]) -> list[dict]:
    """La serie diaria que devuelve `stats_service._serie_por_dia`: `day` es
    un string ISO, no un `date`, y la ventana termina en el día de NOW."""
    fin = NOW.date()
    return [
        {"day": str(fin - timedelta(days=len(counts) - 1 - i)), "count": n}
        for i, n in enumerate(counts)
    ]


_MSGS = (14, 9, 22, 31, 18, 4, 2, 27, 35, 29, 16, 11, 6, 3, 24, 33, 41, 28,
         19, 12, 5, 2, 21, 30, 26, 17, 13, 7, 3, 25)
_ERRS = (0, 0, 1, 0, 0, 0, 0, 2, 0, 0, 0, 1, 0, 0, 0, 0, 3, 0, 0, 0,
         0, 0, 0, 1, 0, 0, 0, 0, 0, 0)
_LEADS = (4, 2, 7, 9, 5, 1, 0, 8, 11, 6, 3, 4, 2, 1, 7, 10, 12, 8, 5, 3,
          1, 0, 6, 9, 7, 4, 3, 2, 1, 7)

STATS = {
    "leads_by_source": {"infocasas": 151, "whatsapp": 49, "telegram": 14, None: 6},
    "weekly_evolution": _serie(_LEADS),
    "events_this_week": {},
    "new_today": 7,
    "days": 30,
    "messages_per_day": _serie(_MSGS),
    "errors_per_day": _serie(_ERRS),
    "conversion_rate": 7.5,
    "conversion_total": 214,
    "conversion_converted": 16,
}

GAP = {
    "days": 30,
    "total_combos": 34,
    "rows": [
        {"city": "Asunción", "ptype": "departamento", "demand": 78, "stock": 96,
         "captar": False, "ptype_slug": "departamento"},
        {"city": "Luque", "ptype": "casa", "demand": 41, "stock": 12,
         "captar": True, "ptype_slug": "casa"},
        {"city": "San Lorenzo", "ptype": "duplex", "demand": 33, "stock": 4,
         "captar": True, "ptype_slug": "casa-duplex"},
        {"city": "Fernando de la Mora", "ptype": "casa", "demand": 26, "stock": 31,
         "captar": False, "ptype_slug": "casa"},
        {"city": "Ciudad del Este", "ptype": "terreno", "demand": 18, "stock": 0,
         "captar": True, "ptype_slug": None},
        {"city": "Capiatá", "ptype": "local", "demand": 11, "stock": 3,
         "captar": True, "ptype_slug": "local-comercial"},
    ],
}


# --- settings --------------------------------------------------------------
BOT_SETTINGS = [
    {"key": "bot_enabled", "value": "true", "updated_at": _d(3),
     "description": "Enciende o apaga el bot en todos los canales"},
    {"key": "whatsapp_mode", "value": "auto", "updated_at": _d(3),
     "description": "auto = el bot contesta WhatsApp; manual = contesta el asesor"},
    {"key": "bot_default_mode", "value": "recepcionista", "updated_at": _d(21),
     "description": "Modo con el que arranca una conversación nueva"},
    {"key": "ic_autoreply_enabled", "value": "false", "updated_at": _d(9),
     "description": "Autorespuesta a los leads que llegan de InfoCasas"},
    {"key": "followup_enabled", "value": "true", "updated_at": _d(9),
     "description": "Seguimiento automático a las 24 y 72 horas"},
    {"key": "ic_reenviados_enabled", "value": "true", "updated_at": _d(30),
     "description": "Reenvío al asesor de los leads de InfoCasas"},
    {"key": "exchange_rate_usd_pyg", "value": "7350", "updated_at": _d(1),
     "description": "Cotización usada para mostrar precios en guaraníes"},
]

AUTH_ROWS = [
    {"id": 401, "created_at": _h(2), "email": "rocio.duarte@onnix.test",
     "ip": "186.16.24.10", "user_agent": "Mozilla/5.0 (Macintosh) Safari/17.4",
     "result": "success"},
    {"id": 400, "created_at": _h(5.5), "email": "gustavo.ramirez@onnix.test",
     "ip": "190.52.160.44", "user_agent": "Mozilla/5.0 (Android 14) Chrome/126",
     "result": "success"},
    {"id": 399, "created_at": _h(26), "email": "gustavo.ramirez@onnix.test",
     "ip": "190.52.160.44", "user_agent": "Mozilla/5.0 (Android 14) Chrome/126",
     "result": "wrong_password"},
    {"id": 398, "created_at": _h(27), "email": "larissa.ojeda@onnix.test",
     "ip": "181.120.8.7", "user_agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/126",
     "result": "locked"},
    {"id": 397, "created_at": _d(2), "email": "hola@onnix.test",
     "ip": "45.132.199.3", "user_agent": None, "result": "not_found"},
]

# --- propiedades -----------------------------------------------------------
# `local_image_count` y `main_image_url` van vacíos a propósito: la foto de una
# propiedad sale del portal de origen o de la cámara de la inmobiliaria
# (`.claude/rules/ui.md`), así que acá no hay ninguna. El macro
# `partials/property_photo.html` dibuja el monograma Onnix, que es lo mismo que
# ve el panel cuando una propiedad no tiene fotos.
def _prop(id_, titulo, tipo, oper, ciudad, barrio, usd, pyg, dorm, banos,
          area, estado, dias, activa=True, hold=False):
    slug = titulo.lower().replace(" ", "-")
    return {
        "id": id_, "source": "onnixpy", "external_id": f"Onnix-{id_}",
        "title": titulo, "url": None,
        "price_usd": Decimal(usd) if usd else None,
        "price_pyg": Decimal(pyg) if pyg else None,
        "price_currency": "USD" if usd else "PYG",
        "city": ciudad, "neighborhood": barrio, "operation": oper,
        "property_type": tipo, "bedrooms": dorm, "bathrooms": banos,
        "total_area_m2": Decimal(area) if area else None,
        "construction_state": estado, "is_active": activa, "on_hold": hold,
        "updated_at": _d(dias), "created_at": _d(dias + 40),
        "portal_listed_at": _d(dias + 40), "portal_expires_at": None,
        "main_image_url": None, "local_image_count": 0,
        "public_path": f"/prop/{id_}-{slug}" if activa and not hold else None,
    }


PROPIEDADES = [
    _prop(4821, "Departamento a estrenar en Villa Morra", "departamento",
          "venta", "Asunción", "Villa Morra", "145000", None, 3, 2, "128.00",
          "entrega_inmediata", 1),
    _prop(4790, "Casa con patio en Lambaré", "casa", "venta", "Lambaré",
          "Santa Ana", "98000", None, 4, 3, "215.50", "usado", 3),
    _prop(4755, "Departamento amoblado frente al Golf", "departamento",
          "alquiler", "Asunción", "Mburucuyá", None, "4200000", 2, 2, "84.00",
          "usado", 6),
    _prop(4702, "Dúplex en San Lorenzo, a dos cuadras de la Ruta 2",
          "casa_duplex", "venta", "San Lorenzo", "Villa Universitaria",
          "78500", None, 3, 2, "162.00", "usado", 11),
    _prop(4688, "Terreno de 480 m² en Luque", "terreno", "venta", "Luque",
          "Zona Aeropuerto", "52000", None, None, None, "480.00", None, 19),
    _prop(4610, "Local comercial sobre Avenida San Blas", "local_comercial",
          "alquiler", "Fernando de la Mora", "Zona Norte", None, "6800000",
          None, 1, "95.00", "usado", 28, hold=True),
]

QS_VACIO = {k: "" for k in (
    "search_text", "operation", "property_type", "city", "neighborhood",
    "price_min", "price_max", "currency", "bedrooms_min", "bathrooms_min",
    "state", "source",
    "construction_state", "updated_within_days", "ia_query", "amenities",
    "barato")}

PROPIEDADES_CTX = {
    "now": NOW,
    "rows": PROPIEDADES,
    "total": 1247,
    "page": 1,
    "per_page": 24,
    "total_pages": 52,
    "filters": {},
    "qs": dict(QS_VACIO, operation="venta", city="Asunción",
               price_min="50000", currency="USD", state="active"),
    "active_chips": [
        {"param": "operation", "label": "Operación", "value": "Venta"},
        {"param": "city", "label": "Ciudad", "value": "Asunción"},
        {"param": "price_min", "label": "Precio mín", "value": "USD 50.000"},
    ],
    "source_options": ["onnixpy", "infocasas", "remax"],
    "state_options": [("active", "Activas"), ("on_hold", "Pausadas"),
                      ("inactive", "Inactivas"), ("all", "Todas")],
    "operation_options": [("venta", "Venta"), ("alquiler", "Alquiler")],
    "property_type_options": ["departamento", "casa", "casa_duplex",
                              "local_comercial", "oficina", "terreno",
                              "galpon", "campo"],
    "currency_options": [("USD", "USD"), ("PYG", "Gs")],
    "construction_state_options": [("en_construccion", "En construcción"),
                                   ("entrega_inmediata", "Entrega inmediata"),
                                   ("pozo", "En pozo"), ("usado", "Usado")],
    "updated_within_options": [(1, "Hoy"), (7, "Últimos 7 días"),
                               (30, "Último mes"), (90, "Últimos 3 meses")],
    "any_filter_active": True,
    "ia_active": False,
    # La pantalla del tutorial muestra el buscador andando, no el aviso de que
    # la IA no pudo responder.
    "ia_unavailable": False,
    "chatbot_enabled": True,
    "state_counts": {"active": 1180, "on_hold": 34, "inactive": 512},
    "empty_hints": None,
    "public_base_url": "https://onnix.com.py",
    "asesor_a_suffix": "?a=1",
}

FICHA = dict(
    PROPIEDADES[0],
    parking=2,
    description=(
        "<p>Departamento a estrenar en el corazón de Villa Morra, a una cuadra "
        "del Shopping del Sol.</p><p>Tres dormitorios, dos baños completos, "
        "cocina equipada y balcón con vista abierta. El edificio tiene piscina, "
        "quincho y sala de reuniones.</p>"
    ),
    agent_name="Larissa Ojeda",
    agent_phone=TEL["larissa"],
    agent_whatsapp=TEL["larissa"],
    # Sin coordenadas a propósito. Con `latitude` y `longitude` la ficha mete
    # un `<iframe>` a google.com/maps (detail.html:439): la captura pasa a
    # depender de la red y de lo que Google dibuje ese día, que es justo lo que
    # un pipeline de video no puede tener. Sin coordenadas la ficha no dibuja
    # el mapa, que es lo mismo que ve el panel con una propiedad sin geocodear.
    latitude=None,
    longitude=None,
    last_scraped_at=_d(1),
    photo_urls=[],
)


# --- leads -----------------------------------------------------------------
def _lead(id_, nombre, tel, fuente, estado, asesor, horas, interes,
          espera=None, prop=None, ic=None, conv=None):
    base = {
        "id": id_, "name": nombre, "phone": tel,
        "email": f"{nombre.split()[0].lower()}@correo.test",
        "source": fuente, "status": estado, "agent_user_id": asesor,
        "agent_assigned_at": _h(horas) if asesor else None,
        # Sin ver: es lo que enciende el badge «Nuevo» del lead asignado.
        "agent_seen_at": None,
        "created_at": _h(horas + 2), "last_activity_at": _h(horas),
        "property_id": None, "consulta_date": None,
        "property_title": None, "property_city": None,
        "property_neighborhood": None, "property_price": None,
        "property_operation": None, "property_url": None,
        "ic_title": None, "ic_city": None, "ic_price_sale": None,
        "ic_price_rent": None, "ic_currency_sale": None,
        "ic_currency_rent": None, "ic_ref": None, "ic_url": None,
        "conversation_id": conv, "search_context": None,
        "is_direct_ic": None, "has_inquiry_history": False,
        "interest_summary": interes,
        "waiting_bucket": espera[0] if espera else None,
        "waiting_since": _h(horas) if espera else None,
        "waiting_label": espera[1] if espera else None,
        "last_contact_at": _h(horas),
        "last_contact_label": espera[1] if espera else f"hace {int(horas)} h",
    }
    if prop:
        base.update(property_id=prop["id"], property_title=prop["title"],
                    property_city=prop["city"],
                    property_neighborhood=prop["neighborhood"],
                    property_price=prop["price_usd"],
                    property_operation=prop["operation"], property_url=None)
    if ic:
        base.update(ic)
    return base


LEADS = [
    _lead(912, "Mariela Ferreira", TEL["mariela"], "whatsapp", "interested",
          3, 3, "Departamento · Venta · Asunción", ("ambar", "hace 3 h"),
          prop=PROPIEDADES[0], conv=77),
    _lead(911, "Derlis Cabañas", TEL["derlis"], "infocasas", "new", None, 5,
          "Casa · Venta · Lambaré", ("rojo", "hace 5 h"),
          ic={"is_direct_ic": True, "consulta_date": _h(5),
              "ic_title": "Casa con patio en Lambaré", "ic_city": "Lambaré",
              "ic_price_sale": Decimal("98000"), "ic_currency_sale": "USD",
              "ic_ref": "IC-99321", "ic_url": None,
              "has_inquiry_history": True}),
    _lead(910, "Nadia Giménez", TEL["nadia"], "telegram", "bot_replied", None,
          9, "Departamento · Alquiler · Asunción", ("verde", "hace 9 h"),
          prop=PROPIEDADES[2], conv=76),
    _lead(909, "Cristian Duarte", TEL["cristian"], "infocasas",
          "agent_replied", 2, 26, "Dúplex · Venta · San Lorenzo",
          ic={"is_direct_ic": False, "consulta_date": _h(26),
              "ic_title": "Dúplex en San Lorenzo", "ic_city": "San Lorenzo",
              "ic_price_sale": Decimal("78500"), "ic_currency_sale": "USD",
              "ic_ref": "IC-98877", "ic_url": None}),
    _lead(908, "Vanessa Rolón", TEL["vanessa"], "vista_publica",
          "visit_scheduled", 3, 50, "Terreno · Venta · Luque",
          prop=PROPIEDADES[4]),
    _lead(907, "Alcides Núñez", TEL["alcides"], "manual", "no_response", None,
          96, "Local · Alquiler · Fernando de la Mora",
          ("rojo", "hace 4 días")),
]

AGENTES_MAP = {2: "Gustavo Ramírez", 3: "Larissa Ojeda"}


# --- contactos -------------------------------------------------------------
def _contacto(id_, nombre, tel, fuente, estado, dias, prop_id=None,
              ic_ref=None, correo=None, consulta=None):
    return {
        "id": id_, "name": nombre, "phone": tel,
        "email": correo, "status": estado, "source": fuente,
        "source_id": ic_ref, "infocasas_ref": ic_ref, "property_id": prop_id,
        "baja_at": None, "created_at": _d(dias), "last_activity_at": _d(dias / 4),
        "agent_user_id": 3 if id_ % 2 else 2,
        # `contacts.first_message` — la ficha lo muestra bajo «Consulta del
        # cliente» desde el 2026-08-24. El entorno de `env()` usa
        # StrictUndefined, asi que el campo TIENE que existir aunque este
        # vacio: sin la clave, `render()` revienta y se caen las 10 pantallas.
        "first_message": consulta,
        "preferences": {"operacion": "compra", "zona": "Villa Morra",
                        "presupuesto": "150000", "dormitorios": "3"},
    }


CONTACTOS = [
    # El texto de la consulta es inventado, como todo lo demas de este archivo:
    # es el bloque que la ficha muestra bajo «Consulta del cliente».
    _contacto(912, "Mariela Ferreira", TEL["mariela"], "whatsapp",
              "interested", 4, prop_id=4821, correo="mariela@correo.test",
              consulta="Hola, vi la publicacion y me interesa. ¿Esta "
                       "disponible para mudarse en septiembre?<br />¿El "
                       "edificio tiene generador y cochera para dos autos?"),
    _contacto(911, "Derlis Cabañas", TEL["derlis"], "infocasas", "new", 2,
              ic_ref="IC-99321",
              consulta="Hola, vi esta propiedad en InfoCasas y me interesa "
                       "recibir mas informacion. ¡Muchas gracias!"),
    _contacto(910, "Nadia Giménez", TEL["nadia"], "telegram", "bot_replied", 8),
    _contacto(909, "Cristian Duarte", TEL["cristian"], "infocasas",
              "agent_replied", 12, ic_ref="IC-98877",
              correo="cristian@correo.test"),
    _contacto(908, "Vanessa Rolón", TEL["vanessa"], "manual",
              "visit_scheduled", 21, prop_id=4688),
    _contacto(907, "Alcides Núñez", TEL["alcides"], "import:excel",
              "no_response", 60),
]

PROPS_MAP = {p["id"]: p for p in PROPIEDADES}
IC_PROPS_MAP = {
    "IC-99321": {"url": "https://www.infocasas.com.py/aviso/99321",
                 "title": "Casa con patio en Lambaré",
                 "price_sale": Decimal("98000"), "currency_sale": "USD",
                 "price_rent": None, "currency_rent": None,
                 "city": "Lambaré", "neighborhood": "Santa Ana",
                 "operation": "venta", "property_type": "casa", "bedrooms": 4},
    "IC-98877": {"url": "https://www.infocasas.com.py/aviso/98877",
                 "title": "Dúplex en San Lorenzo", "price_sale": Decimal("78500"),
                 "currency_sale": "USD", "price_rent": None,
                 "currency_rent": None, "city": "San Lorenzo",
                 "neighborhood": "Villa Universitaria", "operation": "venta",
                 "property_type": "casa_duplex", "bedrooms": 3},
}

PREFIJOS = [("+595", "PY", "Paraguay"), ("+54", "AR", "Argentina"),
            ("+55", "BR", "Brasil"), ("+598", "UY", "Uruguay"),
            ("+1", "US", "Estados Unidos"), ("+34", "ES", "España")]

ESTADOS_FILTRO = ["new", "bot_replied", "agent_replied", "visit_scheduled",
                  "interested", "closed", "no_response", "discarded"]

PHONE_INFO = {"country_code": "+595", "national_number": "991000011",
              "country": "PY", "country_name": "Paraguay",
              "valid": True, "known_prefix": True}


def _evento(tipo, horas, viejo=None, nuevo=None, quien="user:3", meta=None):
    return {"id": 5000 + int(horas), "event_type": tipo, "old_status": viejo,
            "new_status": nuevo, "triggered_by": quien,
            "created_at": _h(horas), "event_metadata": meta or {}}


TIMELINE = [
    {"type": "individual",
     "event": _evento("status_change", 3, "bot_replied", "interested")},
    {"type": "collapsed", "event_type": "message_sent", "triggered_by": "system:bot",
     "count": 4, "representative": _evento("message_sent", 5, quien="system:bot")},
    {"type": "session", "count": 6, "last_activity": _h(9),
     "events": [_evento("message_received", 9, quien="system:bot"),
                _evento("message_sent", 9, quien="system:bot"),
                _evento("property_viewed", 9, quien="system:bot",
                        meta={"property_title": "Departamento a estrenar en Villa Morra"})]},
    {"type": "individual",
     "event": _evento("lead_created", 96, quien="system:infocasas",
                      meta={"lead_name": "Mariela Ferreira", "source": "whatsapp"})},
]

FOLLOWUP = [
    {"kind": "recordatorio", "at": _h(-20), "obj": {
        "id": 31, "due_at": _h(-20), "done_at": None,
        "note": "Llamarla para confirmar la visita del sábado"}},
    {"kind": "nota", "at": _h(6), "obj": {
        "id": 88, "created_at": _h(6),
        "content": "Busca tres dormitorios en Villa Morra o Carmelitas. "
                   "Tiene el crédito preaprobado del banco."}},
    {"kind": "recordatorio", "at": _d(2), "obj": {
        "id": 30, "due_at": _d(2), "done_at": _d(2),
        "note": "Mandarle el link de la ficha"}},
]

BUSQUEDAS = [
    {"fecha": "2026-08-19T11:24:00", "tipo": "departamento",
     "operacion": "venta", "barrio": "Villa Morra", "ciudad": "Asunción",
     "presupuesto_max": 160000, "moneda": "usd", "resultados_encontrados": 7},
    {"fecha": "2026-08-17T18:02:00", "tipo": "casa", "operacion": "venta",
     "barrio": None, "ciudad": "Asunción", "presupuesto_max": 180000,
     "moneda": "usd", "resultados_encontrados": 3},
]

VISTAS = [
    {"id": 4821, "title": "Departamento a estrenar en Villa Morra",
     "city": "Asunción", "neighborhood": "Villa Morra",
     "price_usd": Decimal("145000"), "price_currency": "usd",
     "url": "https://onnix.com.py/prop/4821", "viewed_at": _h(9)},
    {"id": 4790, "title": "Casa con patio en Lambaré", "city": "Lambaré",
     "neighborhood": "Santa Ana", "price_usd": Decimal("98000"),
     "price_currency": "usd", "url": "https://onnix.com.py/prop/4790",
     "viewed_at": _h(30)},
]

CONSULTAS_IC = [
    {"consulta_date": _d(2), "property_title": "Casa con patio en Lambaré",
     "infocasas_ref": "IC-99321", "consulta_id": "99321-1"},
    {"consulta_date": _d(35), "property_title": "Dúplex en San Lorenzo",
     "infocasas_ref": "IC-98877", "consulta_id": "98877-4"},
]


# --- conversaciones --------------------------------------------------------
def _conv_item(id_, nombre, tel, canal, preview, horas, bot=True,
               entrante=True):
    return {
        "conversation": {"id": id_, "channel": canal,
                         "last_message_at": _h(horas), "is_bot_active": bot,
                         "message_count": 12},
        "contact_name": nombre, "contact_phone": tel,
        "last_message_preview": preview,
        "last_message_direction": "inbound" if entrante else "outbound",
        "needs_reply": entrante,
    }


CONVERSACIONES = [
    _conv_item(77, "Mariela Ferreira", TEL["mariela"], "whatsapp",
               "¿El departamento de Villa Morra sigue disponible?", 3),
    _conv_item(76, "Nadia Giménez", TEL["nadia"], "telegram",
               "Perfecto, ¿puedo verlo el sábado a la mañana?", 9, bot=False),
    _conv_item(75, "Derlis Cabañas", TEL["derlis"], "whatsapp",
               "Te paso tres opciones que entran en ese presupuesto.", 27,
               entrante=False),
    _conv_item(74, "Cristian Duarte", TEL["cristian"], "whatsapp",
               "Gracias, lo hablo con mi esposa y te escribo.", 50,
               entrante=False),
    _conv_item(73, "Vanessa Rolón", TEL["vanessa"], "whatsapp",
               "Estoy buscando un terreno en Luque, de 400 m² para arriba.",
               75),
]


def _msg(id_, direccion, quien, texto, horas, intent=None, modelo=None,
         estado="delivered"):
    return {"id": id_, "direction": direccion, "sender_type": quien,
            "body": texto, "content": texto, "intent": intent,
            "ai_model": modelo, "status": estado, "error_message": None,
            "error_code": None, "created_at": _h(horas)}


HILO = {
    "conversation": {"id": 77, "is_bot_active": True},
    "contact": {"id": 912},
    "contact_name": "Mariela Ferreira",
    "contact_phone": TEL["mariela"],
    "window_expired": False,
    "messages": [
        _msg(2101, "inbound", "contact",
             "Hola, buenas. Vi un departamento en Villa Morra en el portal.",
             28, intent="saludo"),
        _msg(2102, "outbound", "bot",
             "¡Hola Mariela! Soy el asistente de Onnix SA. "
             "¿Buscás para **comprar** o para **alquilar**?", 27.9,
             intent="saludo", modelo="claude-sonnet-4-5"),
        _msg(2103, "inbound", "contact",
             "Para comprar. Necesito tres dormitorios, hasta 160 mil dólares.",
             27.5, intent="busqueda"),
        _msg(2104, "outbound", "bot",
             "Encontré 3 opciones que entran en ese presupuesto. "
             "Te paso la que más se acerca:", 27.4, intent="busqueda",
             modelo="claude-sonnet-4-5"),
        _msg(2105, "outbound", "agent",
             "Hola Mariela, soy Larissa de Onnix SA. "
             "Si querés lo vemos el sábado a la mañana.", 4),
        _msg(2106, "inbound", "contact",
             "¿El departamento de Villa Morra sigue disponible?", 3),
    ],
    # `properties_map` se fue con el vertical inmobiliario: el hilo ya no
    # adjunta tarjetas de propiedad.
    "properties_map": {},
}

# MOCK-FIN


# --------------------------------------------------------------------------
# Las pantallas
# --------------------------------------------------------------------------
def _request(path: str) -> SimpleNamespace:
    """Lo único que los templates piden del Request: la ruta (para el ítem
    activo del menú), el token de CSRF y los query params."""
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        state=SimpleNamespace(csrf_token="csrf-de-mentira-0000"),
        query_params=SimpleNamespace(multi_items=lambda: []),
    )


def _chrome(path: str, **extra) -> dict:
    ctx = {"request": _request(path), "user": ADMIN}
    ctx.update(extra)
    return ctx


class Pantalla:
    def __init__(self, id_, template, ruta, titulo, descripcion, ctx):
        self.id = id_
        self.template = template
        self.ruta = ruta
        self.titulo = titulo
        self.descripcion = descripcion
        self.ctx = ctx

    @property
    def archivo(self) -> str:
        return f"{self.id}.html"


def pantallas() -> list[Pantalla]:
    """El orden es el del tutorial, y es el orden del manifest."""
    return [
        Pantalla(
            "login", "login.html", "/login", "Ingreso al panel",
            "La pantalla de entrada: correo y contraseña.",
            {"request": _request("/login"), "error": None},
        ),
        Pantalla(
            "dashboard", "dashboard.html", "/dashboard", "Dashboard",
            "El resumen del día: el embudo de leads, los contadores de las "
            "últimas 24 horas y de dónde vienen las consultas. Sólo lo ve un "
            "administrador.",
            _chrome("/dashboard", stats=DASHBOARD_STATS, demand=DEMAND_30),
        ),
        Pantalla(
            "conversaciones", "conversations.html", "/conversations",
            "Conversaciones",
            "La bandeja: a la izquierda las conversaciones, a la derecha el "
            "hilo abierto con el cliente y el cuadro para contestarle.",
            _chrome(
                "/conversations",
                conversations=CONVERSACIONES,
                selected_id=77,
                thread=HILO,
                whatsapp_mode="auto",
                phone_prefixes=PREFIJOS,
                channel="",
                stuck=False,
                q="",
                offset=0,
                limit=30,
                has_more=True,
            ),
        ),
        Pantalla(
            "leads", "leads.html", "/leads", "Leads",
            "La cola de trabajo: quién consultó, por qué propiedad, hace "
            "cuánto que espera respuesta y a qué asesor está asignado.",
            _chrome(
                "/leads",
                leads=LEADS,
                tab="leads",
                tab_counts={"leads": 161, "interesados": 12, "asignados": 43,
                            "sin_respuesta": 58},
                assignable_users=ASESORES[:2],
                agents_display_map=AGENTES_MAP,
                total=161,
                page=1,
                total_pages=7,
                q=None,
                source=None,
                agent_id=None,
                filter_qs="",
            ),
        ),
        # Las dos pantallas de propiedades se fueron con el vertical
        # inmobiliario, igual que la del portal público.
        Pantalla(
            "contactos-listado", "contacts.html", "/contacts",
            "Base de clientes — listado",
            "Todos los contactos con su estado y por qué canal llegaron.",
            _chrome(
                "/contacts",
                contacts=CONTACTOS,
                props_map=PROPS_MAP,
                infocasas_props_map=IC_PROPS_MAP,
                total=886,
                page=1,
                per_page=50,
                total_pages=18,
                status_filter=None,
                source_filter=None,
                search=None,
                phone_filter=None,
                phone_prefixes=PREFIJOS,
                filter_statuses=ESTADOS_FILTRO,
                badge_map=BADGE_MAP,
                overdue_contacts={912},
            ),
        ),
        Pantalla(
            "contactos-detalle", "contacts_detail.html", "/contacts/912",
            "Base de clientes — ficha del contacto",
            "La historia completa de un cliente: sus datos, lo que buscó, las "
            "propiedades que miró, las notas del asesor y los recordatorios.",
            _chrome(
                "/contacts/912",
                contact=CONTACTOS[0],
                contact_id=912,
                grouped_events=TIMELINE,
                conversations=[{"id": 77, "message_count": 12,
                                "last_message_at": _h(3)}],
                valid_statuses=ESTADOS_FILTRO,
                linked_property=PROPIEDADES[0],
                ic_property=None,
                phone_info=PHONE_INFO,
                phone_prefixes=PREFIJOS,
                search_history=BUSQUEDAS,
                viewed_properties=VISTAS,
                inquiry_history=CONSULTAS_IC,
                users_map={1: "Rocío Duarte", 2: "Gustavo Ramírez",
                           3: "Larissa Ojeda"},
                agents_display_map=AGENTES_MAP,
                has_active_visit=True,
                followup=FOLLOWUP,
                notes=[f["obj"] for f in FOLLOWUP if f["kind"] == "nota"],
                reminders=[f["obj"] for f in FOLLOWUP if f["kind"] != "nota"],
                overdue_ids={31},
            ),
        ),
        Pantalla(
            "stats", "stats.html", "/stats", "Estadísticas",
            "Qué piden los clientes contra qué tenemos en stock, y la "
            "evolución de leads, mensajes y errores por día. Sólo lo ve un "
            "administrador.",
            _chrome("/stats", stats=STATS, demand=DEMAND_90, gap=GAP, days=90),
        ),
        Pantalla(
            "settings", "settings.html", "/settings", "Configuración",
            "Los interruptores del bot, los accesos al panel, el alta de "
            "asesores y los datos de la propia cuenta.",
            _chrome(
                "/settings",
                settings=BOT_SETTINGS,
                bot_enabled=True,
                whatsapp_mode="auto",
                ic_autoreply_enabled=False,
                followup_enabled=True,
                ic_reenviados_enabled=True,
                bot_default_mode="recepcionista",
                rows=AUTH_ROWS,
                total=len(AUTH_ROWS),
                page=1,
                per_page=50,
                filters={"email": None, "ip": None, "date_from": None, "date_to": None},
                filters_querystring="tab=accesos",
                is_currently_locked=False,
                locked_emails_in_view={"larissa.ojeda@onnix.test"},
                users=[ADMIN, *ASESORES],
                user_filters={"search": "", "role": "", "active": True},
                audit_base_url="/settings",
            ),
        ),
    ]


# --------------------------------------------------------------------------
# El render
# --------------------------------------------------------------------------
def env():
    """El entorno de Jinja de la app —los mismos filtros y globals—, con tres
    cambios: el loader apunta al árbol en vez de a `app/templates` relativo,
    `StrictUndefined` para que un dato que falta explote en vez de renderizar
    un hueco, y los dos filtros que leen el reloj fijados a NOW."""
    from jinja2 import FileSystemLoader, StrictUndefined

    from app.tz import get_templates

    e = get_templates().env
    e.loader = FileSystemLoader(str(TEMPLATES))
    e.undefined = StrictUndefined
    e.filters["humandate"] = partial(humandate, now=NOW)
    e.filters["wa_timestamp"] = partial(wa_timestamp, now=NOW)
    return e


_HX_TRIGGER = re.compile(r'hx-trigger="([^"]*)"')


def _sin_disparos_automaticos(html: str) -> str:
    """Apaga los `hx-trigger` que salen solos: `load` y `every Ns`.

    Sin esto la captura se rompe sola. El bloque de visitas
    (`contacts_detail.html:189`) pide `/contacts/912/visits` apenas carga y
    `/stats` se repregunta cada 60 s: servido desde un directorio estático eso
    devuelve un 404, y HTMX mete el cuerpo del 404 adentro de la pantalla —en
    el medio del video. Los disparos que dependen de un click o de un `change`
    no se tocan: esos no ocurren si nadie los provoca.
    """
    def _limpiar(m):
        quedan = [t.strip() for t in m.group(1).split(",")
                  if t.strip() != "load"
                  and not t.strip().startswith(("load ", "every "))]
        return 'hx-trigger="{}"'.format(", ".join(quedan) or "none")

    return _HX_TRIGGER.sub(_limpiar, html)


def render(pantalla: Pantalla, jinja=None) -> str:
    """El HTML de una pantalla, ya con las rutas relativas."""
    jinja = jinja or env()
    html = jinja.get_template(pantalla.template).render(**pantalla.ctx)
    # El HTML vive en la raíz del directorio de salida y `static/` al lado.
    html = html.replace('"/static/', '"static/').replace("'/static/", "'static/")
    return _sin_disparos_automaticos(html)


def _css_del_contenedor(contenedor: str) -> str | None:
    try:
        out = subprocess.run(
            ["docker", "exec", contenedor, "cat", "app/static/css/tailwind.css"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 and out.stdout.strip() else None


def copiar_estaticos(out: Path, contenedor: str | None) -> str:
    """Deja `static/` adentro del directorio de salida y devuelve de dónde
    salió el CSS, para el manifest."""
    destino = out / "static"
    if destino.exists():
        shutil.rmtree(destino)
    shutil.copytree(STATIC, destino)

    origen = ("panel/app/static/css/tailwind.css (el del repo, que NO es "
              "necesariamente el que sirve el contenedor)")
    if contenedor:
        css = _css_del_contenedor(contenedor)
        if css:
            (destino / "css" / "tailwind.css").write_text(css, encoding="utf-8")
            origen = f"docker exec {contenedor} cat app/static/css/tailwind.css"
        else:
            print(
                f"AVISO: no pude leer el CSS de `{contenedor}`; uso el del repo, "
                "que el Dockerfile recompila en el build (panel/Dockerfile:44-46) "
                "y puede no tener las clases arbitrarias.",
                file=sys.stderr,
            )

    # Las hojas viven en `static/css/`, así que `/static/fonts/x` es `../fonts/x`.
    for hoja in (destino / "css").glob("*.css"):
        texto = hoja.read_text(encoding="utf-8")
        hoja.write_text(texto.replace("/static/", "../"), encoding="utf-8")
    return origen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT,
                    help=f"directorio de salida (default: {OUT_DEFAULT})")
    ap.add_argument("--css-from", default="onnix-panel", metavar="CONTENEDOR",
                    help="contenedor del que sacar el tailwind.css compilado "
                         "(default: onnix-panel; vacío para usar el del repo)")
    args = ap.parse_args(argv)

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    origen_css = copiar_estaticos(out, args.css_from or None)

    jinja = env()
    lista = pantallas()
    for p in lista:
        (out / p.archivo).write_text(render(p, jinja), encoding="utf-8")

    manifest = {
        "generado_por": "scripts/render_panel_mock.py",
        "css_de": origen_css,
        "pantallas": [
            {"id": p.id, "archivo": p.archivo, "titulo": p.titulo,
             "ruta_real": p.ruta, "descripcion": p.descripcion}
            for p in lista
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    filas = "\n".join(
        f'    <li><a href="{p.archivo}">{p.titulo}</a> — {p.descripcion}</li>'
        for p in lista
    )
    (out / "index.html").write_text(
        '<!DOCTYPE html>\n<html lang="es">\n<head><meta charset="utf-8">'
        "<title>Panel Onnix — capturas con datos inventados</title></head>\n"
        "<body>\n  <h1>Panel Onnix — datos inventados</h1>\n  <ul>\n"
        f"{filas}\n  </ul>\n</body>\n</html>\n",
        encoding="utf-8",
    )

    print(f"{len(lista)} pantallas en {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

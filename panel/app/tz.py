"""Timezone utilities for Paraguay (America/Asuncion)."""
import html
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy import literal_column

_BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
# Un salto de línea suelto —no dos— es envoltura del portal de origen, no un
# párrafo: se convierte en espacio. Ver clean_description().
_SINGLE_NEWLINE_RE = re.compile(r"(?<!\n)\n(?!\n)")

PYT_NAME = "America/Asuncion"
PYT = ZoneInfo(PYT_NAME)

# El corte de dia del panel, en SQL, se escribe siempre igual. Las columnas
# `created_at`/`updated_at` son `timestamptz`, asi que:
#   timestamptz AT TIME ZONE 'America/Asuncion' -> timestamp SIN huso (hora local)
#   timestamp   AT TIME ZONE 'America/Asuncion' -> timestamptz (el instante)
# El viaje de ida y vuelta importa: si el borde de la ventana se deja como
# `timestamp` pelado, Postgres lo interpreta con el TimeZone de la sesion
# (UTC en el contenedor) y la ventana queda corrida tres horas respecto del
# agrupamiento. Dia:  (col AT TIME ZONE 'America/Asuncion')::date
# Mes:  date_trunc('month', col AT TIME ZONE 'America/Asuncion')
#
# TRAMPA, verificada contra un Postgres 16: `<date> AT TIME ZONE 'zona'` NO
# hace lo que parece. Postgres castea el `date` a `timestamptz` con el huso de
# la sesion y despues lo pasa a hora local, o sea que
# `(now() AT TIME ZONE 'America/Asuncion')::date AT TIME ZONE 'America/Asuncion'`
# devuelve medianoche PYT corrida TRES HORAS PARA ATRAS. El borde de una
# ventana de dias sale de Python (`pyt_day_start`), no de esa expresion; el de
# meses usa `date_trunc`, que devuelve `timestamp` y ahi el viaje si es exacto.
#
# Para el SQL armado con SQLAlchemy va `func.timezone(PYT_SQL_ZONE, col)`.
# El huso viaja como literal y NO como bind param a proposito: `timezone()`
# esta sobrecargada (text e interval) y con un parametro sin tipo la
# resolucion depende de reglas de categoria — con el literal no hay duda.
PYT_SQL_ZONE = literal_column(f"'{PYT_NAME}'")


def pyt_day_start(days_ago: int = 0, *, now: datetime | None = None) -> datetime:
    """Medianoche de Paraguay de hoy —o de hace *days_ago* dias— con huso.

    Devuelve un datetime aware, listo para comparar contra una columna
    ``timestamptz``. Sirve para el "hoy" del panel: entre las 21:00 y las
    23:59 PYT el dia UTC ya cambio y ``CURRENT_DATE`` corta donde no debe.
    """
    day = (now.astimezone(PYT) if now else datetime.now(PYT)).date()
    day -= timedelta(days=days_ago)
    return datetime(day.year, day.month, day.day, tzinfo=PYT)


def pyt_month_start(*, now: datetime | None = None) -> datetime:
    """Primer instante del mes calendario paraguayo en curso, con huso."""
    ref = (now.astimezone(PYT) if now else datetime.now(PYT))
    return datetime(ref.year, ref.month, 1, tzinfo=PYT)


_MONTH_ES = {
    1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
    7: "jul", 8: "ago", 9: "sep", 10: "oct", 11: "nov", 12: "dic",
}

_DAY_ES = {
    0: "Lunes", 1: "Martes", 2: "Miercoles", 3: "Jueves",
    4: "Viernes", 5: "Sabado", 6: "Domingo",
}


def to_pyt(dt, fmt="%d/%m/%Y %H:%M"):
    """Convert a datetime to Paraguay time and format it.

    Usage in Jinja2: {{ dt|pyt }} or {{ dt|pyt('%H:%M') }}
    """
    if dt is None:
        return "\u2014"
    if dt.tzinfo is not None:
        dt = dt.astimezone(PYT)
    return dt.strftime(fmt)


def humandate(dt, *, now: datetime | None = None):
    """Format a date in short Spanish: '12 mar' or '5 ene 2025'.

    Omits the year when it matches the current year.
    Accepts datetime, date, or ISO-format string.

    `now` fija el "hoy" contra el que se decide si el ano se omite — mismo
    parametro que `wa_timestamp` y `pyt_day_start`, y por el mismo motivo: sin
    el, la salida de un render cambia sola al cambiar el ano.
    Lo usa `scripts/render_panel_mock.py`, que tiene que dar bytes identicos
    en dos corridas.

    Usage in Jinja2: {{ entry.day|humandate }}
    """
    if dt is None:
        return "\u2014"
    if isinstance(dt, str):
        try:
            dt = datetime.strptime(dt[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return dt
    if hasattr(dt, "date"):
        if dt.tzinfo is not None:
            dt = dt.astimezone(PYT)
        dt = dt.date()
    month = _MONTH_ES.get(dt.month, str(dt.month))
    today = (now.astimezone(PYT) if now else datetime.now(PYT)).date()
    if dt.year == today.year:
        return f"{dt.day} {month}"
    return f"{dt.day} {month} {dt.year}"


def wa_timestamp(dt, *, now: datetime | None = None):
    """WhatsApp-style relative timestamp for conversation lists.

    Today: "HH:MM", Yesterday: "Ayer", 2-6 days: day name,
    7+ same year: "DD/MM", different year: "DD/MM/YY".
    """
    if dt is None:
        return "\u2014"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc).astimezone(PYT)
    else:
        dt = dt.astimezone(PYT)
    if now is None:
        now = datetime.now(PYT)
    delta = (now.date() - dt.date()).days
    if delta <= 0:
        return dt.strftime("%H:%M")
    if delta == 1:
        return "Ayer"
    if 2 <= delta <= 6:
        return _DAY_ES[dt.weekday()]
    if dt.year == now.year:
        return dt.strftime("%d/%m")
    return dt.strftime("%d/%m/%y")


def strip_markdown(text: str) -> str:
    """Remove Markdown formatting markers for plain-text display.

    Handles bold, italic, strikethrough, inline code, headers, bullets,
    and links.  Emojis and plain text (including Spanish accented chars)
    are preserved unchanged.
    Safe against truncated markers (e.g. ``"Hola **negrita"``).

    WhatsApp-specific patterns covered:
      *bold*  _italic_  ~strikethrough~  `code`
    Telegram/standard Markdown:
      **bold**  __bold__  _italic_

    Usage in Jinja2: {{ item.last_message_preview | strip_markdown }}
    """
    if not text:
        return text
    # Inline code: `code` — strip before bold/italic so backticks don't confuse them
    text = re.sub(r'`([^`]*)`', r'\1', text)
    # Strikethrough: ~text~ (WhatsApp)
    text = re.sub(r'~(.+?)~', r'\1', text)
    # Bold: **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    # Italic: *text* or _text_
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    # Headers: # Heading
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Bullets: - item or * item
    text = re.sub(r'^[-*]\s+', '', text, flags=re.MULTILINE)
    # Links: [text](url)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    # Collapse extra whitespace produced by stripping markers
    text = re.sub(r'  +', ' ', text)
    return text.strip()


def render_markdown(text: str) -> Markup:
    """Convert WhatsApp-style Markdown to safe HTML for chat bubble rendering.

    Converts **bold** and _italic_ markers to HTML tags.
    HTML-escapes content first to prevent XSS.
    Returns Markup so Jinja2 won't double-escape the result.

    Usage in Jinja2: {{ msg.body | render_markdown }}
    """
    if not text:
        return Markup("")
    # Escape HTML entities FIRST (XSS prevention)
    safe = str(escape(text))
    # Bold: **text** or __text__
    safe = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', safe, flags=re.DOTALL)
    safe = re.sub(r'__(.+?)__', r'<strong>\1</strong>', safe, flags=re.DOTALL)
    # Italic: *text* (don't match ** bold markers)
    safe = re.sub(r'(?<!\*)\*(.+?)(?<!\*)\*', r'<em>\1</em>', safe, flags=re.DOTALL)
    # Italic: _text_
    safe = re.sub(r'_(.+?)_', r'<em>\1</em>', safe, flags=re.DOTALL)
    return Markup(safe)


def clean_description(text: str | None) -> str:
    """Convert portal-ingested HTML descriptions to plain text with real newlines.

    InfoCasas and similar sources persist literal ``<br />`` markers and
    stray ``\\r`` characters in property descriptions. The detail template
    renders with ``whitespace-pre-line`` so it expects plain text broken by
    real ``\\n``.

    Steps:
      1. Replace ``<br>`` / ``<br/>`` / ``<br />`` (any case) with ``\\n``.
      2. Strip remaining HTML tags (XSS defense, e.g. ``<script>``).
      3. Drop ``\\r`` and decode HTML entities (``&aacute;`` -> ``á``).
      4. Collapse 3+ consecutive blank lines into one paragraph break.

    Returns "" for None/empty input so the template can decide whether to
    show the description card.
    """
    if not text:
        return ""
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _BLANK_LINES_RE.sub("\n\n", text)
    # Un salto solo es el ancho de OTRO sistema, no un párrafo nuestro.
    # Medido el 2026-08-23 sobre una ficha real: 1.332 caracteres ocupaban 57
    # líneas en el celular —23 caracteres por línea— cuando en esa columna
    # entran 48,7. El texto venía envuelto a mano por el portal de origen, con
    # su ancho, y `white-space: pre-line` lo respetaba: el bloque medía el doble
    # de lo que le corresponde. Dos saltos siguen siendo un párrafo.
    text = _SINGLE_NEWLINE_RE.sub(" ", text)
    return text.strip()


def get_templates():
    """Return Jinja2Templates with PYT timezone filter registered."""
    # Import local: constants no importa nada de app, pero mantenerlo local
    # evita cualquier ciclo si algun dia lo hace.
    from app.constants import BADGE_MAP
    from app.utils.money import miles, precio

    t = Jinja2Templates(directory="app/templates")
    # Global, no contexto: partials/status_badge.html se incluye desde
    # lead_item y contact_status_block, y esas vistas las renderizan
    # rutas distintas. Pasarlo por contexto obligaba a que cada una se acordara.
    t.env.globals["badge_map"] = BADGE_MAP
    # Un solo formato de número en todo el panel y el portal (app/utils/money.py).
    # `url_foto` se fue con el vertical inmobiliario: armaba el path de la foto
    # de una propiedad. Si Meta trae adjuntos, el armador nuevo va acá mismo.
    t.env.globals["miles"] = miles
    t.env.globals["precio"] = precio
    t.env.filters["pyt"] = to_pyt
    t.env.filters["humandate"] = humandate
    t.env.filters["wa_timestamp"] = wa_timestamp
    t.env.filters["strip_markdown"] = strip_markdown
    t.env.filters["render_markdown"] = render_markdown
    t.env.filters["clean_description"] = clean_description
    return t

"""
Application-wide constants for the Onnix SA panel.

Single source of truth for values that were previously duplicated across
routes, services, and templates.
"""

# All valid contact statuses (excludes the soft-delete sentinel "deleted").
# frozenset: supports `in` membership tests and equals a plain set() in comparisons.
#
# GSD v17: "contacted" split into bot_replied + agent_replied.
# "visit_scheduled" and "negotiation" removed (0 usage confirmed 2026-04-05).
# 2026-05-23 (M6.0/CLEAN-07): negotiation removed from get_hot_leads IN filter.
# 2026-05-27 (M6.2/VISIT-09): visit_scheduled REINTRODUCED as hybrid Option C flag,
#                              auto-synced by VisitService when ≥1 active visit.
VALID_STATUSES: frozenset[str] = frozenset({
    "new",
    "bot_replied",
    "agent_replied",
    "visit_scheduled",   # M6.2 (mig 040) — reintroduced as hybrid Option C flag.
    "interested",
    "closed",
    "no_response",
    "discarded",
})

# Extended set including the soft-delete sentinel — used by Pydantic schemas
# that must accept "deleted" as a valid status value.
VALID_STATUSES_WITH_DELETED: frozenset[str] = VALID_STATUSES | {"deleted"}

# Badge display configuration for contact statuses.
# Each entry: (bg_class, text_class, label)
# Used in Python routes to render inline HTML badge spans.
# NOTE: panel/app/templates/partials/status_badge.html maintains a parallel
# definition in Jinja2 dict syntax — keep both in sync when adding/removing statuses.
# (variante, etiqueta). Antes guardaba clases de Tailwind y
# partials/status_badge.html mantenia una copia paralela en Jinja que habia que
# sincronizar a mano — el propio archivo lo pedia en un comentario. Ahora la
# variante la resuelve el CSS y esta es la unica fuente.
#
# Las cuatro variantes, y por que cada estado cae donde cae:
#   strong  — exige accion ahora. Es lo mas oscuro de la pantalla.
#   default — esta pasando, no pide nada.
#   quiet   — terminal: retrocede para no competir por atencion.
#   danger  — SOLO irreversible (ui.md reserva el rojo para destructivo).
BADGE_MAP: dict[str, tuple[str, str]] = {
    "new":             ("strong",  "Nuevo"),
    "interested":      ("strong",  "Interesado"),
    "bot_replied":     ("default", "Bot respondió"),
    "agent_replied":   ("default", "Agente escribió"),
    "visit_scheduled": ("default", "Visita agendada"),
    "closed":          ("quiet",   "Cerrado"),
    "no_response":     ("quiet",   "Sin respuesta"),
    "discarded":       ("quiet",   "Descartado"),
    # soft-delete sentinel — not in VALID_STATUSES but needed for badge rendering
    "deleted":         ("quiet",   "Eliminado"),
}

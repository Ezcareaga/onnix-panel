"""ToolExecutor — dispatches AI tool calls to the service layer.

Maps Claude's tool_call names to SearchService operations and returns
JSON-serializable result dicts. Also builds tool_result messages in the
Anthropic SDK format for the tool-use loop.

Plan 62-04: CORE-01 ToolExecutor.
Fase E (M5): AlternativesBuilder integration behind feature flag
  m5_zero_results_alternatives_enabled.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from app.bot.ai.types import ToolCall
from app.bot.core.types import ConversationState
from app.bot.middleware.injection_guard import sanitize_tool_output
from app.bot.search.sql_filters import SearchFilters
from app.services.lead_event_service import record_event
from app.services.visit_service import VisitService

from app.bot.search.alternatives import _EXCLUDED_FROM_ACTIVE_COUNT

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.bot.search.search_service import SearchService
    from app.bot.search.alternatives import AlternativesBuilder
    from app.repositories.bot_setting_repo import BotSettingRepository

logger = logging.getLogger(__name__)

_MISSING = object()


def _filters_subset_match(alt_filters: dict, current_filters: dict) -> bool:
    """True si cada key de alt_filters matchea current_filters con semántica list-order-insensitive."""
    for k, alt_v in alt_filters.items():
        cur_v = current_filters.get(k, _MISSING)
        if cur_v is _MISSING:
            return False
        if isinstance(alt_v, list) and isinstance(cur_v, list):
            if sorted(alt_v) != sorted(cur_v):
                return False
        else:
            if alt_v != cur_v:
                return False
    return True


class _DecimalEncoder(json.JSONEncoder):
    """JSON encoder that converts Decimal to float."""

    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)

# Ordinal resolution map (Spanish ordinals -> 0-based index)
ORDINALS: dict[str, int] = {
    "la primera": 0,
    "primera": 0,
    "la 1": 0,
    "la segunda": 1,
    "segunda": 1,
    "la 2": 1,
    "la tercera": 2,
    "tercera": 2,
    "la 3": 2,
}


class ToolExecutor:
    """Dispatches tool calls from Claude to the appropriate service.

    Supports: search_properties, get_property_detail, register_lead.
    Unknown tools return a descriptive error dict instead of raising.
    """

    def __init__(
        self,
        search_service: "SearchService",
        alternatives_builder: "AlternativesBuilder | None" = None,
        bot_settings_repo: "BotSettingRepository | None" = None,
    ) -> None:
        self._search_service = search_service
        self._alternatives_builder = alternatives_builder
        self._bot_settings_repo = bot_settings_repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        tool_call: ToolCall,
        session: "AsyncSession",
        search_context: ConversationState | None = None,
    ) -> dict:
        """Dispatch a tool call and return its result as a dict."""
        dispatch = {
            "search_properties": self._execute_search,
            "get_property_detail": self._execute_detail,
            "register_lead": self._execute_lead,
            "process_opt_out": self._execute_opt_out,
            "resolver_zona": self._execute_resolver_zona,
            "agendar_visita": self._execute_agendar_visita,
        }

        handler = dispatch.get(tool_call.name)
        if handler is None:
            logger.warning("Unknown tool: %s", tool_call.name)
            return {"error": f"Herramienta desconocida: {tool_call.name}"}

        if tool_call.name == "get_property_detail":
            return await handler(tool_call.input, session, search_context)
        if tool_call.name in ("register_lead", "process_opt_out"):
            return await handler(tool_call.input)
        # search_properties, resolver_zona, agendar_visita all take the
        # (input, session, search_context) arg shape.
        return await handler(tool_call.input, session, search_context)

    def build_tool_result_message(
        self,
        tool_call: ToolCall,
        result: dict,
    ) -> dict:
        """Build a tool_result message in Anthropic SDK format.

        Sanitizes tool output before serialization to strip internal
        fields and limit data sent back to the AI model.
        """
        sanitized = sanitize_tool_output(result)
        return {
            "type": "tool_result",
            "tool_use_id": tool_call.id,
            "content": json.dumps(sanitized, ensure_ascii=False, cls=_DecimalEncoder),
        }

    # ------------------------------------------------------------------
    # Private dispatch handlers
    # ------------------------------------------------------------------

    async def _execute_search(
        self,
        input_data: dict,
        session: "AsyncSession",
        search_context: ConversationState | None = None,
    ) -> dict:
        """Execute search_properties via SearchService."""
        # Defensive copy so we never mutate the caller's ToolCall.input dict
        input_data = {**input_data}

        # Defense-2: inherit tipo from context when Claude omits it
        if (
            not input_data.get("tipo")
            and search_context is not None
            and isinstance(search_context.filtros.get("tipo"), str)
            and search_context.filtros["tipo"]
        ):
            inherited_tipo = search_context.filtros["tipo"]
            input_data["tipo"] = inherited_tipo
            logger.info("Tool executor inherited tipo — %s", inherited_tipo)

        # Accept both canonical names and aliases (e.g. estado_construccion).
        # model_fields contains canonical names; build a union set with aliases.
        _field_aliases: set[str] = {
            f.alias
            for f in SearchFilters.model_fields.values()
            if f.alias is not None
        }
        _valid_keys = set(SearchFilters.model_fields.keys()) | _field_aliases
        filters = SearchFilters(**{
            k: v for k, v in input_data.items()
            if k in _valid_keys
        })

        if (
            not filters.construction_state
            and filters.descripcion_libre
            and "pozo" in filters.descripcion_libre.lower()
        ):
            filters.construction_state = "en_pozo"

        # Exclude already-shown properties to avoid repeats
        if search_context and search_context.shown_properties:
            filters.excluded_ids = list(search_context.shown_properties)

        # --- Fase I (M5): zero_results_accepted — text trigger ---
        # If the user had pending alternatives and the filters Claude just sent
        # contain all the filters from one of those alternatives (exact subset
        # match), the user typed something that matched the suggestion — record
        # acceptance BEFORE executing the search, then clear the alternatives so
        # they don't leak into future turns.
        if (
            search_context is not None
            and search_context.pending_alternatives
            and search_context._contact_id is not None
        ):
            current_dump = filters.model_dump(exclude_none=True)
            for alt in search_context.pending_alternatives:
                alt_filters: dict = alt.get("filters", {})
                # Accept if every key/value in the alt's filters appears in the
                # current call — list-order-insensitive subset match.
                if alt_filters and _filters_subset_match(alt_filters, current_dump):
                    await record_event(
                        session,
                        contact_id=search_context._contact_id,
                        conversation_id=search_context._conversation_id,
                        event_type="zero_results_accepted",
                        trigger="text",
                        metadata={
                            "alt_id": alt.get("id"),
                            "trigger": "text",
                        },
                    )
                    # Clear: user chose by typing, alternatives consumed
                    search_context.pending_alternatives = []
                    search_context.pending_alternatives_age = 0
                    break
        # --- end Fase I text trigger ---

        result = await self._search_service.search_properties(filters, session)

        # --- Fase E (M5): zero-results alternatives behind feature flag ---
        if (
            result.total_found == 0
            and self._alternatives_builder is not None
            and self._bot_settings_repo is not None
        ):
            flag_on = await self._bot_settings_repo.get_bool(
                session,
                "m5_zero_results_alternatives_enabled",
                default=False,
            )
            if flag_on:
                # Count active filters (exclude defaults that aren't real constraints)
                dumped = filters.model_dump(exclude_none=True)
                active_keys = [
                    k for k in dumped
                    if k not in _EXCLUDED_FROM_ACTIVE_COUNT
                ]
                if len(active_keys) >= 2:
                    # Resolve geo the same way SearchService does it
                    barrios_list = (
                        ([filters.barrio] if filters.barrio else [])
                        + (filters.barrios or [])
                    )
                    geo = self._search_service._geo_resolver.resolve(
                        city=filters.ciudad,
                        barrios=barrios_list or None,
                    )
                    alt_result = await self._alternatives_builder.build(
                        session, filters, geo,
                    )
                    if alt_result.alternatives:
                        alts_serialized = [asdict(a) for a in alt_result.alternatives]
                        # Persist in search_context (caller flushes to DB)
                        if search_context is not None:
                            search_context.pending_alternatives = alts_serialized
                            search_context.pending_alternatives_age = 0
                        # --- Fase I (M5): emit zero_results_offered ---
                        if (
                            search_context is not None
                            and search_context._contact_id is not None
                        ):
                            await record_event(
                                session,
                                contact_id=search_context._contact_id,
                                conversation_id=search_context._conversation_id,
                                event_type="zero_results_offered",
                                trigger="zero_results",
                                metadata={
                                    "filters": filters.model_dump(exclude_none=True),
                                    "alternatives_count": len(alts_serialized),
                                    "alt_ids": [a["id"] for a in alts_serialized],
                                },
                            )
                        # --- end Fase I offered ---
                        return {
                            "properties": [],
                            "total_found": 0,
                            "all_ids": [],
                            "alternatives": alts_serialized,
                        }
                    # No useful alternatives → fall through to legacy fallback
        # --- end Fase E ---

        # Return first 2 for display, all IDs for pagination tracking
        display_props = result.properties[:2]
        all_ids = [p["id"] for p in result.properties]

        result_dict = {
            "properties": display_props,
            "total_found": result.total_found,
            "all_ids": all_ids,
        }
        if result.price_stats:
            result_dict["price_stats"] = result.price_stats
        if result.degradation and result.degradation.min_price_in_zone is not None:
            result_dict["min_price_in_zone"] = result.degradation.min_price_in_zone
            result_dict["no_results_message"] = result.degradation.description
        if (
            result.degradation
            and result.degradation.level in (1, 2, 3)
            and result.degradation.relaxed_filters
        ):
            result_dict["degradation_level"] = result.degradation.level
            result_dict["relaxed_filters"] = list(result.degradation.relaxed_filters)
        return result_dict

    async def _execute_detail(
        self,
        input_data: dict,
        session: "AsyncSession",
        context: ConversationState | None = None,
    ) -> dict:
        """Execute get_property_detail via SearchService."""
        referencia = input_data.get("referencia", "")
        property_id = self._resolve_referencia(referencia, context)

        if property_id is None:
            return {"error": "No pude identificar la propiedad solicitada."}

        result = await self._search_service.get_by_ids([property_id], session)

        if not result.properties:
            return {"error": "Propiedad no encontrada."}

        return result.properties[0]

    async def _execute_lead(self, input_data: dict) -> dict:
        """Handle register_lead — actual DB insert done by Orchestrator."""
        return {
            "success": True,
            "motivo": input_data.get("motivo", ""),
            "message": "Lead registrado",
        }

    async def _execute_opt_out(self, input_data: dict) -> dict:
        """Handle process_opt_out — actual DB writes done by Orchestrator."""
        return {
            "success": True,
            "message": "Opt-out registrado",
        }

    async def _execute_resolver_zona(
        self,
        input_data: dict,
        session: "AsyncSession",
        search_context: ConversationState | None = None,
    ) -> dict:
        """Ejecuta la tool resolver_zona.

        Resuelve texto ambiguo de zona a ciudad/barrio/landmark canonical
        reutilizando la instancia GeoResolver del SearchService.
        """
        texto = input_data.get("texto", "").strip()
        if not texto:
            return {"error": "texto requerido"}

        geo = self._search_service._geo_resolver

        # 1. Intentar landmark primero (más específico)
        landmark = geo.resolve_landmark(texto)

        # 2. Normalizar + resolver alias ciudad
        normed = geo.normalize(texto)
        ciudad_canonica: str | None = None
        if geo.is_known_city(normed):
            ciudad_canonica = geo.resolve_city_alias(normed)

        # 3. Si no es ciudad, intentar barrio (necesita ciudad contexto)
        barrio_canonico: str | None = None
        if not ciudad_canonica and search_context and search_context.filtros.get("ciudad"):
            ciudad_ctx = search_context.filtros["ciudad"]
            resolved = geo.resolve_barrio_alias(normed)
            # Validate that the resolved barrio actually exists in the context city
            city_barrios = geo.data.barrios.get(geo.normalize(ciudad_ctx), {})
            if resolved in city_barrios:
                barrio_canonico = resolved
            # If not found, barrio_canonico stays None — Claude interprets "zona no reconocida"

        # 4. Vecinos cercanos
        vecinos: list[str] = []
        if ciudad_canonica:
            vecinos = [
                loc.name
                for loc in geo.expand_city_neighbors(ciudad_canonica, max_distance=1)
                if loc.distance > 0
            ][:5]
        elif barrio_canonico and search_context and search_context.filtros.get("ciudad"):
            ciudad_ctx = search_context.filtros["ciudad"]
            vecinos = [
                loc.name
                for loc in geo.expand_barrio_neighbors(barrio_canonico, ciudad_ctx)
                if loc.distance > 0
            ][:5]

        # Fallback: landmark detectado sin ciudad/barrio en filtros
        # → poblar vecinos desde el barrio del landmark.
        if not vecinos and landmark and landmark.barrio and landmark.ciudad:
            vecinos = [
                loc.name
                for loc in geo.expand_barrio_neighbors(landmark.barrio, landmark.ciudad)
                if loc.distance > 0
            ][:5]

        # Landmark display info — map empty string to None explicitly
        landmark_ciudad = (landmark.ciudad if landmark else None) or None
        landmark_barrio = (landmark.barrio if landmark else None) or None

        return {
            "ciudad_canonica": ciudad_canonica,
            "barrio_canonico": barrio_canonico,
            "landmark_detected": landmark_barrio,
            "landmark_ciudad": landmark_ciudad,
            "barrios_cercanos": vecinos,
            "interpretation": (
                f"'{texto}' resuelto a "
                f"ciudad={ciudad_canonica or '?'}, "
                f"barrio={barrio_canonico or '?'}, "
                f"landmark={landmark_barrio or '?'}"
            ),
        }

    async def _execute_agendar_visita(
        self,
        input_data: dict,
        session: "AsyncSession",
        search_context: ConversationState | None = None,
    ) -> dict:
        """Schedule a visit (BOT-05/BOT-13 + D-1).

        Claude never sees contact ids — the contact_id is injected from
        search_context. Bot-created visits use agent_user_id=None (NULL,
        unassigned) and source='bot' (D-1). Any VisitService error is
        returned as a {'error': ...} tool result, NEVER surfaced to the
        user verbatim (CLAUDE.md UX rule #5).
        """
        contact_id = (
            search_context._contact_id if search_context is not None else None
        )
        if contact_id is None:
            return {"error": "contacto no identificado"}

        try:
            scheduled_at = datetime.fromisoformat(input_data["scheduled_at_iso"])
        except (ValueError, KeyError, TypeError):
            return {"error": "fecha inválida"}
        if scheduled_at.tzinfo is None:
            # Naive datetime would compare-error against tz-aware NOW in the
            # service; reject up front rather than risk an unhandled raise.
            return {"error": "fecha sin zona horaria"}

        property_id = input_data.get("property_id")
        visit, err = await VisitService.create_visit(
            session,
            contact_id=contact_id,
            scheduled_at=scheduled_at,
            agent_user_id=None,        # D-1: NULL (unassigned)
            property_id=property_id,
            source="bot",
        )
        if err:
            return {"error": err}      # tool result, NOT user-facing
        return {
            "ok": True,
            "visit_id": visit.id,
            "scheduled_at": scheduled_at.isoformat(),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_referencia(
        referencia: str,
        context: ConversationState | None,
    ) -> int | None:
        """Resolve a property reference to an integer ID.

        Handles:
        - Numeric strings ("12345")
        - Spanish ordinals ("la primera", "segunda") using current_page_ids
        """
        ref = referencia.strip().lower()

        # Try numeric first
        try:
            return int(ref)
        except ValueError:
            pass

        # Try ordinal resolution
        if context is not None and context.current_page_ids:
            idx = ORDINALS.get(ref)
            if idx is not None and idx < len(context.current_page_ids):
                return context.current_page_ids[idx]

        return None

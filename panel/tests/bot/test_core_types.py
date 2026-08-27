"""Tests for core bot types.

Covers: BotRequest, BotResponse, ConversationState, HistoryMessage,
ContactInfo, ConversationInfo, ChannelPayload, PayloadMessage.
"""
import sys
from pathlib import Path

# Ensure panel/ on sys.path
_panel_dir = str(Path(__file__).resolve().parent.parent.parent)
if _panel_dir not in sys.path:
    sys.path.insert(0, _panel_dir)

from app.bot.core.types import (
    BotRequest,
    BotResponse,
    ConversationState,
    HistoryMessage,
    ContactInfo,
    ConversationInfo,
    ChannelPayload,
    PayloadMessage,
)


# -----------------------------------------------------------------------
# TestBotRequest (4 tests)
# -----------------------------------------------------------------------

class TestBotRequest:
    def test_bot_request_telegram(self):
        req = BotRequest(
            platform="telegram",
            chat_id="12345",
            user_id="67890",
            user_name="Test User",
            text="Hola",
            external_id="msg_001",
        )
        assert req.platform == "telegram"
        assert req.chat_id == "12345"
        assert req.user_id == "67890"
        assert req.user_name == "Test User"
        assert req.text == "Hola"
        assert req.external_id == "msg_001"

    def test_bot_request_whatsapp(self):
        req = BotRequest(
            platform="whatsapp",
            chat_id="+595981000001",
            user_id="+595981000001",
            user_name="WA User",
            text="Busco casa",
            external_id="SM123",
        )
        assert req.platform == "whatsapp"

    def test_bot_request_callback(self):
        req = BotRequest(
            platform="telegram",
            chat_id="12345",
            user_id="67890",
            user_name="Test User",
            callback_data="detail_1",
        )
        assert req.callback_data == "detail_1"
        assert req.text is None

    def test_bot_request_defaults(self):
        req = BotRequest(
            platform="telegram",
            chat_id="12345",
            user_id="67890",
            user_name="Test User",
        )
        assert req.callback_data is None
        assert req.external_id is None
        assert req.text is None


# -----------------------------------------------------------------------
# TestBotResponse (4 tests)
# -----------------------------------------------------------------------

class TestBotResponse:
    def test_bot_response_text_only(self):
        resp = BotResponse(text="Hola!", intent="saludo")
        assert resp.text == "Hola!"
        assert resp.intent == "saludo"
        assert resp.properties == []
        assert resp.buttons == []

    def test_bot_response_with_properties(self):
        resp = BotResponse(
            text="Encontre propiedades",
            intent="busqueda",
            properties=[{"id": 1, "title": "Casa"}],
            shown_ids=[1],
            pending_ids=[2, 3],
        )
        assert resp.properties == [{"id": 1, "title": "Casa"}]
        assert resp.shown_ids == [1]
        assert resp.pending_ids == [2, 3]

    def test_bot_response_with_ai_metadata(self):
        resp = BotResponse(
            text="Respuesta IA",
            intent="saludo",
            ai_model="claude-haiku-4-5",
            ai_tokens_in=100,
            ai_tokens_out=50,
        )
        assert resp.ai_model == "claude-haiku-4-5"
        assert resp.ai_tokens_in == 100
        assert resp.ai_tokens_out == 50

    def test_bot_response_is_error(self):
        resp = BotResponse(text="Error", intent="error", is_error=True)
        assert resp.is_error is True
        # default should be False
        resp2 = BotResponse(text="OK", intent="saludo")
        assert resp2.is_error is False


# -----------------------------------------------------------------------
# TestConversationState (6 tests)
# -----------------------------------------------------------------------

class TestConversationState:
    def test_empty_state(self):
        state = ConversationState()
        assert state.filtros == {}
        assert state.shown_properties == []
        assert state.resultados_pendientes == []
        assert state.etapa == "inicio"

    def test_from_jsonb(self):
        data = {
            "filtros": {"ciudad": "Asuncion", "operacion": "venta"},
            "etapa": "mostrando_resultados",
            "shown_properties": [1, 2],
            "resultados_pendientes": [3, 4, 5],
        }
        state = ConversationState.from_jsonb(data)
        assert state.filtros == {"ciudad": "Asuncion", "operacion": "venta"}
        assert state.etapa == "mostrando_resultados"
        assert state.shown_properties == [1, 2]
        assert state.resultados_pendientes == [3, 4, 5]

    def test_from_jsonb_null(self):
        state_none = ConversationState.from_jsonb(None)
        assert state_none.etapa == "inicio"
        assert state_none.filtros == {}

        state_empty = ConversationState.from_jsonb({})
        assert state_empty.etapa == "inicio"
        assert state_empty.filtros == {}

    def test_to_jsonb(self):
        state = ConversationState()
        state.filtros = {"ciudad": "Asuncion"}
        state.shown_properties = [1, 2, 3]
        result = state.to_jsonb()
        assert isinstance(result, dict)
        assert result["filtros"] == {"ciudad": "Asuncion"}
        assert result["shown_properties"] == [1, 2, 3]
        assert "ultima_actualizacion" in result
        # timestamp should be ISO format string
        assert "T" in result["ultima_actualizacion"]

    def test_shown_properties_cap(self):
        state = ConversationState()
        state.shown_properties = list(range(60))
        result = state.to_jsonb()
        assert len(result["shown_properties"]) == 50
        # Should keep the LAST 50
        assert result["shown_properties"] == list(range(10, 60))

    def test_shown_properties_under_cap_not_truncated(self):
        state = ConversationState()
        state.shown_properties = list(range(30))
        result = state.to_jsonb()
        assert len(result["shown_properties"]) == 30
        assert result["shown_properties"] == list(range(30))

    def test_merge_filters(self):
        state = ConversationState()
        state.filtros = {"ciudad": "Asuncion", "operacion": "venta"}
        state.merge_filters({"barrio": "villa morra", "precio_max": 200000})
        assert state.filtros["ciudad"] == "Asuncion"
        assert state.filtros["operacion"] == "venta"
        assert state.filtros["barrio"] == "villa morra"
        assert state.filtros["precio_max"] == 200000

    # --- search_shown_count (pagination counter fix) ---

    def test_search_shown_count_default(self):
        """search_shown_count defaults to 0."""
        state = ConversationState()
        assert state.search_shown_count == 0

    def test_search_shown_count_serialized(self):
        """to_jsonb includes search_shown_count."""
        state = ConversationState()
        state.search_shown_count = 6
        result = state.to_jsonb()
        assert result["search_shown_count"] == 6

    def test_search_shown_count_from_jsonb(self):
        """from_jsonb deserializes search_shown_count."""
        data = {"search_shown_count": 4, "etapa": "mostrando_resultados"}
        state = ConversationState.from_jsonb(data)
        assert state.search_shown_count == 4

    def test_search_shown_count_backward_compat(self):
        """from_jsonb works with old data missing search_shown_count."""
        data = {"etapa": "inicio", "filtros": {}}
        state = ConversationState.from_jsonb(data)
        assert state.search_shown_count == 0

    # --- Task 85-01: total_found, last_search_at, lead_registrado ---

    def test_new_fields_defaults(self):
        """New fields have correct defaults."""
        state = ConversationState()
        assert state.total_found == 0
        assert state.last_search_at is None
        assert state.lead_registrado is False

    def test_to_jsonb_includes_new_fields(self):
        """to_jsonb serializes total_found, last_search_at, lead_registrado."""
        state = ConversationState()
        state.total_found = 47
        state.last_search_at = "2026-03-30T12:00:00+00:00"
        state.lead_registrado = True
        result = state.to_jsonb()
        assert result["total_found"] == 47
        assert result["last_search_at"] == "2026-03-30T12:00:00+00:00"
        assert result["lead_registrado"] is True

    def test_to_jsonb_new_fields_defaults(self):
        """to_jsonb serializes default values for new fields."""
        state = ConversationState()
        result = state.to_jsonb()
        assert result["total_found"] == 0
        assert result["last_search_at"] is None
        assert result["lead_registrado"] is False

    def test_from_jsonb_with_new_fields(self):
        """from_jsonb deserializes new fields correctly."""
        data = {
            "etapa": "mostrando_resultados",
            "filtros": {"ciudad": "Asuncion"},
            "total_found": 23,
            "last_search_at": "2026-03-30T10:00:00+00:00",
            "lead_registrado": True,
        }
        state = ConversationState.from_jsonb(data)
        assert state.total_found == 23
        assert state.last_search_at == "2026-03-30T10:00:00+00:00"
        assert state.lead_registrado is True

    def test_from_jsonb_backward_compat_without_new_fields(self):
        """from_jsonb still works with old data missing the new fields."""
        data = {
            "etapa": "inicio",
            "filtros": {"operacion": "venta"},
            "shown_properties": [1, 2],
        }
        state = ConversationState.from_jsonb(data)
        assert state.total_found == 0
        assert state.last_search_at is None
        assert state.lead_registrado is False

    def test_from_jsonb_ignores_unknown_keys_still(self):
        """from_jsonb still ignores unknown keys (regression check)."""
        data = {
            "etapa": "inicio",
            "unknown_future_field": "whatever",
            "total_found": 10,
        }
        state = ConversationState.from_jsonb(data)
        assert state.total_found == 10
        assert not hasattr(state, "unknown_future_field")

    # --- Task 87-01: removed last_shown_ids and resultados_mostrados ---

    def test_to_jsonb_excludes_removed_fields(self):
        """to_jsonb output must not contain last_shown_ids or resultados_mostrados."""
        state = ConversationState()
        state.shown_properties = [1, 2, 3]
        result = state.to_jsonb()
        assert "last_shown_ids" not in result
        assert "resultados_mostrados" not in result

    def test_from_jsonb_backward_compat_old_fields(self):
        """from_jsonb succeeds when old data contains removed fields (backward compat)."""
        data = {
            "etapa": "mostrando_resultados",
            "filtros": {"ciudad": "Asuncion"},
            "last_shown_ids": [10, 11],
            "resultados_mostrados": [10, 11, 12],
            "shown_properties": [10, 11, 12],
        }
        state = ConversationState.from_jsonb(data)
        assert state.etapa == "mostrando_resultados"
        assert state.shown_properties == [10, 11, 12]
        assert not hasattr(state, "last_shown_ids")
        assert not hasattr(state, "resultados_mostrados")


# -----------------------------------------------------------------------
# TestHistoryMessage (2 tests)
# -----------------------------------------------------------------------

class TestHistoryMessage:
    def test_history_message_fields(self):
        msg = HistoryMessage(
            direction="inbound",
            sender_type="contact",
            body="Busco casa",
            properties_shown=None,
        )
        assert msg.direction == "inbound"
        assert msg.sender_type == "contact"
        assert msg.body == "Busco casa"
        assert msg.properties_shown is None

    def test_history_message_format(self):
        inbound = HistoryMessage(
            direction="inbound", sender_type="contact", body="Busco casa"
        )
        assert inbound.format() == "Usuario: Busco casa"

        outbound = HistoryMessage(
            direction="outbound", sender_type="bot", body="Aqui tenes opciones"
        )
        assert outbound.format() == "Bot: Aqui tenes opciones"


# -----------------------------------------------------------------------
# TestContactInfo (3 tests)
# -----------------------------------------------------------------------

class TestContactInfo:
    def test_contact_info_fields(self):
        info = ContactInfo(
            id=1,
            name="Test",
            phone="+595981000001",
            status="new",
            is_baja=False,
            platform="telegram",
            source_id="12345",
        )
        assert info.id == 1
        assert info.name == "Test"
        assert info.phone == "+595981000001"
        assert info.status == "new"
        assert info.is_baja is False
        assert info.platform == "telegram"
        assert info.source_id == "12345"

    def test_contact_info_is_baja(self):
        info = ContactInfo(
            id=2,
            name="Discarded",
            status="discarded",
            is_baja=True,
        )
        assert info.is_baja is True

    def test_contact_info_defaults(self):
        info = ContactInfo(id=3, name="Minimal", status="new")
        assert info.phone is None
        assert info.source_id is None
        assert info.is_baja is False
        assert info.platform == ""

    def test_contact_info_exposes_agent_user_id_field(self):
        """ContactInfo must expose agent_user_id and round-trip the value."""
        info = ContactInfo(id=10, name="Agent Lead", status="agent_replied", agent_user_id=42)
        assert info.agent_user_id == 42

    def test_contact_info_agent_user_id_defaults_to_none(self):
        """agent_user_id defaults to None so existing constructors don't break."""
        info = ContactInfo(id=11, name="No Agent", status="new")
        assert info.agent_user_id is None


# -----------------------------------------------------------------------
# TestConversationInfo (2 tests)
# -----------------------------------------------------------------------

class TestConversationInfo:
    def test_conversation_info_fields(self):
        info = ConversationInfo(
            id=1,
            contact_id=1,
            platform="telegram",
            chat_id="12345",
            is_bot_active=True,
            is_open=True,
            search_context={},
            message_count=5,
        )
        assert info.id == 1
        assert info.contact_id == 1
        assert info.platform == "telegram"
        assert info.chat_id == "12345"
        assert info.is_bot_active is True
        assert info.is_open is True
        assert info.search_context == {}
        assert info.message_count == 5

    def test_conversation_info_defaults(self):
        info = ConversationInfo(
            id=2,
            contact_id=1,
            platform="whatsapp",
            chat_id="+595981000001",
        )
        assert info.is_bot_active is True
        assert info.is_open is True
        assert info.message_count == 0


# -----------------------------------------------------------------------
# TestChannelPayload (3 tests)
# -----------------------------------------------------------------------

class TestChannelPayload:
    def test_channel_payload_text_only(self):
        payload = ChannelPayload(
            messages=[PayloadMessage(text="Hola!")]
        )
        assert len(payload.messages) == 1
        assert payload.messages[0].text == "Hola!"

    def test_channel_payload_with_photos(self):
        payload = ChannelPayload(
            messages=[
                PayloadMessage(
                    text="Casa en Asuncion",
                    photo_url="https://onnix.com.py/images/onnix/123/0.webp",
                )
            ]
        )
        assert payload.messages[0].photo_url == "https://onnix.com.py/images/onnix/123/0.webp"

    def test_channel_payload_with_buttons(self):
        payload = ChannelPayload(
            messages=[
                PayloadMessage(
                    text="Opciones",
                    buttons=[{"text": "Ver mas", "callback_data": "detail_1"}],
                )
            ]
        )
        assert payload.messages[0].buttons[0]["text"] == "Ver mas"
        assert payload.messages[0].buttons[0]["callback_data"] == "detail_1"

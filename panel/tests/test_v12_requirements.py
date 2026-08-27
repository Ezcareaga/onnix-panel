"""
Integration test suite verifying v12 milestone requirements.

Each test class corresponds to a requirement group.
Tests are organized by REQ-ID and check the implementation exists and behaves correctly.

Branch context: test/e2e-v12
Cherry-picked phases: 97 (wa_timestamp), 99 (send_template), 103 (SSE EventBus+endpoint)
NOT cherry-picked: 100 (lead action buttons), 101 (IC welcome timestamps),
                   102 (manual WA send drawer + SEND-07 callbacks),
                   SSE-04 frontend, SSE-05 nginx dedicated block
"""
import re
from pathlib import Path
from pathlib import Path as _Path

REPO_ROOT = _Path(__file__).resolve().parent.parent.parent

import os
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


# ============================================================
# CONV — Conversaciones UI
# ============================================================

class TestCONV01_WaTimestamp:
    """CONV-01: WhatsApp-style timestamps in conversation list."""

    def test_wa_timestamp_today_returns_hhmm(self):
        """Today: HH:MM format."""
        from app.tz import wa_timestamp, PYT
        now = datetime(2026, 4, 3, 15, 0, tzinfo=PYT)
        dt = datetime(2026, 4, 3, 10, 30, tzinfo=PYT)
        result = wa_timestamp(dt, now=now)
        assert result == "10:30"

    def test_wa_timestamp_yesterday_returns_ayer(self):
        """Yesterday: 'Ayer'."""
        from app.tz import wa_timestamp, PYT
        now = datetime(2026, 4, 3, 12, 0, tzinfo=PYT)
        dt = datetime(2026, 4, 2, 20, 0, tzinfo=PYT)
        result = wa_timestamp(dt, now=now)
        assert result == "Ayer"

    def test_wa_timestamp_this_week_returns_day_name(self):
        """2-6 days ago: Spanish day name."""
        from app.tz import wa_timestamp, PYT
        now = datetime(2026, 4, 3, 12, 0, tzinfo=PYT)   # Friday
        dt = datetime(2026, 4, 1, 10, 0, tzinfo=PYT)    # Wednesday, 2 days ago
        result = wa_timestamp(dt, now=now)
        assert result == "Miercoles"

    def test_wa_timestamp_older_same_year_returns_ddmm(self):
        """7+ days same year: DD/MM."""
        from app.tz import wa_timestamp, PYT
        now = datetime(2026, 4, 3, 12, 0, tzinfo=PYT)
        dt = datetime(2026, 3, 1, 10, 0, tzinfo=PYT)
        result = wa_timestamp(dt, now=now)
        assert result == "01/03"

    def test_wa_timestamp_different_year_returns_ddmmyy(self):
        """Different year: DD/MM/YY."""
        from app.tz import wa_timestamp, PYT
        now = datetime(2026, 4, 3, 12, 0, tzinfo=PYT)
        dt = datetime(2025, 12, 25, 10, 0, tzinfo=PYT)
        result = wa_timestamp(dt, now=now)
        assert result == "25/12/25"

    def test_wa_timestamp_filter_registered(self):
        """wa_timestamp filter must be registered in Jinja2 env."""
        from app.tz import get_templates
        env = get_templates().env
        assert "wa_timestamp" in env.filters


# ============================================================
# LEAD — Lead Action Buttons
# ============================================================

class TestLEAD01_VerInmueble:
    """LEAD-01: Ver inmueble button in lead row (Phase 100)."""

    def test_property_url_in_base_columns(self):
        """property_url must be in _BASE_COLUMNS."""
        from app.repositories.lead_repo import _BASE_COLUMNS
        assert "property_url" in _BASE_COLUMNS

    def test_lead_row_template_exists(self):
        """lead_item.html template must exist."""
        path = f"{REPO_ROOT}/panel/app/templates/partials/lead_item.html"
        assert os.path.exists(path)

    def test_lead_row_has_property_url_logic(self):
        """lead_item.html must contain property_url reference (Phase 100 adds action column)."""
        with open(f"{REPO_ROOT}/panel/app/templates/partials/lead_item.html") as f:
            content = f.read()
        assert "property_url" in content


class TestLEAD02_VerConversacion:
    """LEAD-02: Ver conversacion button in lead row (Phase 100)."""

    def test_conversation_id_in_base_columns(self):
        """conversation_id subquery must be in _BASE_COLUMNS."""
        from app.repositories.lead_repo import _BASE_COLUMNS
        assert "conversation_id" in _BASE_COLUMNS

    def test_lead_row_has_conversation_link(self):
        """lead_item.html must contain /conversations link (Phase 100 adds action column)."""
        with open(f"{REPO_ROOT}/panel/app/templates/partials/lead_item.html") as f:
            content = f.read()
        assert "conversations?selected_id" in content or "conversation_id" in content


class TestLEAD03_WaWeb:
    """LEAD-03: WA Web button with pre-filled message (Phase 100)."""

    def test_lead_row_has_wa_me_link(self):
        """lead_item.html must contain wa.me link (Phase 100 adds action column)."""
        with open(f"{REPO_ROOT}/panel/app/templates/partials/lead_item.html") as f:
            content = f.read()
        assert "wa.me" in content

    def test_leads_table_colspan_is_7(self):
        """leads_table.html empty state colspan must be 7 once action column is added."""
        with open(f"{REPO_ROOT}/panel/app/templates/partials/leads_table.html") as f:
            content = f.read()
        assert 'colspan="7"' in content


# ============================================================
# IC — InfoCasas Pipeline
# ============================================================

class TestIC01_WelcomeConversation:
    """IC-01: InfoCasas welcome creates conversation and saves outbound message (Phase 101)."""

    def test_send_whatsapp_welcome_is_not_commented_out(self):
        """_send_whatsapp_welcome() call must be active (not commented out)."""
        with open(
            f"{REPO_ROOT}/panel/app/bot/services/infocasas/infocasas_service.py"
        ) as f:
            content = f.read()
        assert "await self._send_whatsapp_welcome(" in content
        lines = content.splitlines()
        for line in lines:
            stripped = line.strip()
            if "await self._send_whatsapp_welcome(" in stripped and not stripped.startswith("#"):
                return
        pytest.fail("_send_whatsapp_welcome call is commented out")

    def test_save_welcome_message_uses_sent_at(self):
        """_save_welcome_message must accept sent_at parameter."""
        import inspect
        from app.bot.services.infocasas.infocasas_service import InfocasasService
        sig = inspect.signature(InfocasasService._save_welcome_message)
        assert "sent_at" in sig.parameters

    @pytest.mark.asyncio
    async def test_save_welcome_passes_created_at_to_message_repo(self):
        """_save_welcome_message must pass created_at=sent_at to message_repo.create()."""
        from app.bot.services.infocasas.infocasas_service import InfocasasService

        sent_at = datetime(2026, 4, 3, 10, 0, tzinfo=timezone.utc)
        mock_conv = MagicMock()
        mock_conv.id = 42

        with patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as MockMgr, patch(
            "app.bot.services.infocasas.infocasas_service.message_repo"
        ) as mock_repo:
            mock_mgr = MockMgr.return_value
            mock_mgr.get_or_create_conversation = AsyncMock(return_value=mock_conv)
            mock_repo.create = AsyncMock()
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            service = InfocasasService.__new__(InfocasasService)
            service._session_factory = MagicMock(return_value=mock_session)
            await service._save_welcome_message(
                contact_id=1, phone="+595981111111",
                name="Test", zone="Asuncion", sent_at=sent_at
            )

        call_kwargs = mock_repo.create.call_args.kwargs
        assert call_kwargs.get("created_at") == sent_at
        assert call_kwargs.get("intent") == "ic_welcome"
        assert call_kwargs.get("sender_type") == "bot"


# ============================================================
# WA — WhatsApp Templates
# ============================================================

class TestWA_Templates:
    """WA-01 through WA-04: Template keys in ALLOWED_TEMPLATE_KEYS."""

    def test_wa_tpl_send_property_in_allowed_keys(self):
        """WA-01: wa_tpl_send_property must be in ALLOWED_TEMPLATE_KEYS."""
        from app.schemas.template import ALLOWED_TEMPLATE_KEYS
        assert "wa_tpl_send_property" in ALLOWED_TEMPLATE_KEYS

    def test_wa_tpl_send_preferences_in_allowed_keys(self):
        """WA-02: wa_tpl_send_preferences must be in ALLOWED_TEMPLATE_KEYS."""
        from app.schemas.template import ALLOWED_TEMPLATE_KEYS
        assert "wa_tpl_send_preferences" in ALLOWED_TEMPLATE_KEYS

    def test_wa_tpl_send_generic_in_allowed_keys(self):
        """WA-03: wa_tpl_send_generic must be in ALLOWED_TEMPLATE_KEYS."""
        from app.schemas.template import ALLOWED_TEMPLATE_KEYS
        assert "wa_tpl_send_generic" in ALLOWED_TEMPLATE_KEYS

    def test_wa_tpl_followup_v3_in_allowed_keys(self):
        """WA-04: wa_tpl_followup_v3 must be in ALLOWED_TEMPLATE_KEYS.

        Note: legacy `wa_tpl_followup` (sin sufijo) was removed in M4 post-cleanup
        — bot interno migró a `_v3` y los agentes nunca lo usaron desde el panel.
        """
        from app.schemas.template import ALLOWED_TEMPLATE_KEYS
        assert "wa_tpl_followup_v3" in ALLOWED_TEMPLATE_KEYS
        assert "wa_tpl_followup" not in ALLOWED_TEMPLATE_KEYS


# ============================================================
# SEND — Manual Template Sending
# ============================================================

class TestSEND01_DrawerButton:
    """SEND-01: (+) button / drawer trigger in conversations view (Phase 102)."""

    def test_conversations_html_has_nuevo_button(self):
        """conversations.html must contain a + or Nuevo button."""
        with open(f"{REPO_ROOT}/panel/app/templates/conversations.html") as f:
            content = f.read()
        assert "drawerOpen" in content or "Nuevo" in content

    def test_send_template_drawer_template_exists(self):
        """send_template_drawer.html partial must exist."""
        assert os.path.exists(
            f"{REPO_ROOT}/panel/app/templates/partials/send_template_drawer.html"
        )


_DRAWER = f"{REPO_ROOT}/panel/app/templates/partials/send_template_drawer.html"

# Los comentarios nombran lo que el test prohibe, asi que se filtran antes de
# assertar: si no, el test falla contra su propia documentacion.
_COMMENT_RE = re.compile(r"<!--.*?-->|\{#.*?#\}", re.DOTALL)


def _drawer_markup() -> str:
    """El drawer sin comentarios HTML ni Jinja."""
    return _COMMENT_RE.sub("", Path(_DRAWER).read_text(encoding="utf-8"))


class TestSEND02_DrawerCards:
    """SEND-02: las cards de plantilla del drawer (Phase 102).

    Eran tres. La tercera ('Contactar nuevo') vivia detras de un
    `x-show="false"` desde que se escribio: el boton no se podia tocar y su
    formulario se renderizaba en cada carga sin poder mostrarse nunca. Se fue
    con el carril D. Quedan dos, y son las dos que se usan.
    """

    def test_drawer_has_two_option_buttons(self):
        markup = _drawer_markup()
        assert "propiedad" in markup.lower()
        assert "zona" in markup.lower()

    def test_drawer_has_no_permanently_hidden_branches(self):
        """`x-show="false"` es markup que se renderiza y no se ve nunca."""
        assert 'x-show="false"' not in _drawer_markup()

    def test_drawer_has_no_drawer_from_conversation(self):
        """La bandera se declaraba, se reseteaba y nunca se ponia en true:
        sus dos ramas 'desde la conversacion' eran inalcanzables."""
        assert "drawerFromConversation" not in _drawer_markup()
        conv = _COMMENT_RE.sub(
            "",
            Path(f"{REPO_ROOT}/panel/app/templates/conversations.html").read_text(
                encoding="utf-8"
            ),
        )
        assert "drawerFromConversation" not in conv


class TestSEND0305_TemplateForms:
    """SEND-03 through SEND-05: Template form flows (Phase 102)."""

    def test_drawer_has_form1_and_form2(self):
        """Quedan los dos pasos alcanzables; form3 se fue con su card."""
        markup = _drawer_markup()
        assert "form1" in markup
        assert "form2" in markup
        assert "form3" not in markup

    def test_drawer_forms_target_send_template_endpoint(self):
        """Forms must POST to /conversations/send_template."""
        with open(
            f"{REPO_ROOT}/panel/app/templates/partials/send_template_drawer.html"
        ) as f:
            content = f.read()
        assert "/conversations/send_template" in content


class TestSEND06_Endpoint:
    """SEND-06: POST /conversations/send_template endpoint (Phase 99)."""

    def test_send_template_route_is_registered(self):
        """POST /conversations/send_template must be registered."""
        from app.routes.conversations import router
        routes = [
            r for r in router.routes
            if hasattr(r, "path") and "send_template" in r.path
        ]
        assert len(routes) >= 1

    def test_template_service_exists(self):
        """template_service.py must exist and have send_template method."""
        from app.services.template_service import TemplateService
        assert hasattr(TemplateService, "send_template")

    def test_allowed_template_keys_count(self):
        """ALLOWED_TEMPLATE_KEYS = 3 legacy v12 (sin followup) + 10 M3 (v3/v4) = 13.

        wa_tpl_followup fue removido en M4 post-cleanup tras switchover del
        scheduler a wa_tpl_followup_v3. Los demás legacy v12 quedan como
        backward compat hasta que Ez/la administradora confirmen que no los usan.
        """
        from app.schemas.template import ALLOWED_TEMPLATE_KEYS
        legacy_v12 = {
            "wa_tpl_send_property",
            "wa_tpl_send_preferences",
            "wa_tpl_send_generic",
        }
        assert legacy_v12.issubset(ALLOWED_TEMPLATE_KEYS)
        assert "wa_tpl_followup" not in ALLOWED_TEMPLATE_KEYS
        assert len(ALLOWED_TEMPLATE_KEYS) == 13

    def test_send_template_request_rejects_invalid_key(self):
        """SendTemplateRequest must reject invalid template_key."""
        from pydantic import ValidationError
        from app.schemas.template import SendTemplateRequest
        with pytest.raises(ValidationError):
            SendTemplateRequest(contact_id=1, template_key="wa_tpl_unknown")

    def test_send_template_request_accepts_valid_key(self):
        """SendTemplateRequest must accept wa_tpl_send_generic."""
        from app.schemas.template import SendTemplateRequest
        req = SendTemplateRequest(contact_id=1, template_key="wa_tpl_send_generic")
        assert req.template_key == "wa_tpl_send_generic"


# TestSEND07_Callbacks (Phase 102) validated legacy v12 callbacks that were
# deleted in M4 Task 1.1 after audit confirmed 0 uses in 60 days. See
# docs/AUDIT_M4_FASE0_20260419.md §3.2 and the drift guard at
# panel/tests/bot/test_callback_translations.py


# ============================================================
# SSE — Real-Time Updates
# ============================================================

class TestSSE01_EventBus:
    """SSE-01: EventBus singleton (Phase 103)."""

    @pytest.mark.asyncio
    async def test_event_bus_singleton_exists(self):
        from app.services.event_bus import event_bus
        assert event_bus is not None

    @pytest.mark.asyncio
    async def test_subscribe_returns_queue(self):
        import asyncio
        from app.services.event_bus import EventBus
        bus = EventBus()
        q = bus.subscribe()
        assert isinstance(q, asyncio.Queue)
        assert q.maxsize == 50

    @pytest.mark.asyncio
    async def test_publish_delivers_event(self):
        from app.services.event_bus import EventBus
        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("conversation_update", {"conversation_id": 1})
        event = q.get_nowait()
        assert event["type"] == "conversation_update"
        assert event["data"]["conversation_id"] == 1

    @pytest.mark.asyncio
    async def test_queuefull_is_tolerated(self):
        import asyncio
        from app.services.event_bus import EventBus
        bus = EventBus()
        q = bus.subscribe()
        # Fill the queue to capacity
        for _ in range(50):
            q.put_nowait({"type": "x", "data": {}})
        # Publish to a full queue must not raise
        await bus.publish("overflow", {"x": 1})


class TestSSE02_Endpoint:
    """SSE-02: GET /conversations/sse endpoint (Phase 103)."""

    def test_sse_route_registered(self):
        from app.routes.conversations import router
        sse_routes = [
            r for r in router.routes
            if hasattr(r, "path") and r.path == "/conversations/sse"
        ]
        assert len(sse_routes) == 1


class TestSSE04_Frontend:
    """SSE-04: Frontend SSE integration (Phase 103 frontend — not cherry-picked)."""

    def test_conversations_html_has_sse_connect(self):
        with open(f"{REPO_ROOT}/panel/app/templates/conversations.html") as f:
            content = f.read()
        assert "sse-connect" in content

    def test_conversations_html_has_sse_trigger(self):
        with open(f"{REPO_ROOT}/panel/app/templates/conversations.html") as f:
            content = f.read()
        assert "sse:conversation_update" in content


    def test_base_html_has_htmx_sse_extension(self):
        with open(f"{REPO_ROOT}/panel/app/templates/base.html") as f:
            content = f.read()
        assert "htmx-ext-sse" in content or "sse.js" in content


class TestSSE05_Nginx:
    """SSE-05: Nginx SSE location block (Phase 103 ops — not applied yet)."""

    def test_nginx_has_sse_location_block(self):
        with open(f"{REPO_ROOT}/nginx_prod_v7.conf") as f:
            content = f.read()
        assert "location /conversations/sse" in content
        assert "proxy_read_timeout 86400" in content
        assert "proxy_buffering off" in content


# ============================================================
# Cross-cutting: message_repo.create() extended params (Phase 99)
# ============================================================

class TestMessageRepoExtension:
    """Verifies message_repo.create() accepts intent and created_at (Phase 99)."""

    def test_create_signature_has_intent(self):
        import inspect
        from app.repositories.message_repo import MessageRepository
        sig = inspect.signature(MessageRepository.create)
        assert "intent" in sig.parameters

    def test_create_signature_has_created_at(self):
        import inspect
        from app.repositories.message_repo import MessageRepository
        sig = inspect.signature(MessageRepository.create)
        assert "created_at" in sig.parameters

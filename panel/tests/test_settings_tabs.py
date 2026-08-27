"""Tests for /settings 4-tab shell (items 3-6).

Verifies:
  - Tab shell renders with all four tab buttons (admin)
  - Tab keys appear in URL param pattern
  - Bot tab: contains settings_form content
  - Accesos tab: contains auth-audit filter form (admin only)
  - Usuarios tab: contains users table + "Crear usuario" button (admin only)
  - Mi Cuenta tab: contains password change form (all roles)
  - Non-admin sees Mi Cuenta tab but not accesos/usuarios tab buttons
"""
import pytest


class TestSettingsTabShell:
    async def test_admin_sees_all_four_tabs(self, admin_client):
        resp = await admin_client.get("/settings")
        assert resp.status_code == 200
        body = resp.text
        assert "Configuración del Bot" in body
        assert "Accesos" in body
        assert "Usuarios" in body
        assert "Mi Cuenta" in body

    async def test_tab_keys_present_in_template(self, admin_client):
        resp = await admin_client.get("/settings")
        body = resp.text
        for tab_key in ("bot", "accesos", "usuarios", "mi-cuenta"):
            assert tab_key in body, f"tab key '{tab_key}' not found in settings template"

    async def test_bot_tab_contains_settings_form(self, admin_client):
        resp = await admin_client.get("/settings")
        body = resp.text
        # settings_form.html has bot toggle
        assert "bot-toggle" in body
        assert "Estado del Bot" in body

    async def test_accesos_tab_contains_audit_filter_form(self, admin_client):
        resp = await admin_client.get("/settings?tab=accesos")
        body = resp.text
        assert "Auditoría de Login" in body
        # filter form action points to /settings
        assert 'action="/settings"' in body

    async def test_usuarios_tab_contains_crear_usuario(self, admin_client):
        resp = await admin_client.get("/settings?tab=usuarios")
        body = resp.text
        assert "Crear usuario" in body
        assert "settings-users-table" in body

    async def test_mi_cuenta_tab_contains_password_form(self, admin_client):
        resp = await admin_client.get("/settings?tab=mi-cuenta")
        body = resp.text
        assert "Cambiar contraseña" in body
        assert 'name="current_password"' in body
        assert 'name="new_password"' in body
        assert 'name="confirm_password"' in body
        assert "/me/password" in body

    async def test_user_role_can_access_settings(self, user_client):
        """role=user can now access /settings (GET uses get_current_user, not require_admin).
        They only see the Mi Cuenta tab — admin-only tabs are gated in the template."""
        resp = await user_client.get("/settings")
        assert resp.status_code == 200
        body = resp.text
        assert "Mi Cuenta" in body
        assert "Cambiar contraseña" in body

    async def test_unauthenticated_redirects(self, client):
        resp = await client.get("/settings")
        assert resp.status_code == 303

    async def test_filter_form_has_hidden_tab_input(self, admin_client):
        """GET /settings?tab=accesos filter form must include hidden tab=accesos
        so that submitting via GET preserves the active tab in the query string."""
        resp = await admin_client.get("/settings?tab=accesos")
        assert resp.status_code == 200
        assert 'name="tab" value="accesos"' in resp.text

    async def test_empty_email_query_param_does_not_repopulate_input(
        self, admin_client
    ):
        """Fix 2: GET /settings?tab=accesos&email= (empty string) must NOT
        pre-fill the email input with any prior value. The input value
        attribute must be empty so the filter form shows a clean state."""
        resp = await admin_client.get("/settings?tab=accesos&email=")
        assert resp.status_code == 200
        body = resp.text
        # The email input must not carry a stale non-empty value.
        # We just ensure value="" (or no value attr) — not value="some@old.email".
        assert 'value="some@old.email"' not in body
        # The form must have autocomplete="off" on the filter form.
        assert 'autocomplete="off"' in body

    async def test_filter_form_inputs_have_autocomplete_off(self, admin_client):
        """Fix 2: filter form must carry autocomplete=off to prevent browser
        autofill from re-inserting a previously submitted email."""
        resp = await admin_client.get("/settings?tab=accesos")
        assert resp.status_code == 200
        assert 'autocomplete="off"' in resp.text


class TestUsuariosTabUXFix:
    """M6.1 UX fixes — modal close, toast, filters in Usuarios tab."""

    async def test_base_html_has_show_toast_function(self, admin_client):
        """Global toast is defined in base.html body x-data."""
        resp = await admin_client.get("/settings")
        assert resp.status_code == 200
        assert "showToast" in resp.text

    async def test_modal_form_has_htmx_after_request_handler(self, admin_client):
        """The Crear usuario form has the @htmx:after-request auto-close handler."""
        resp = await admin_client.get("/settings?tab=usuarios")
        assert resp.status_code == 200
        assert "htmx:after-request" in resp.text

    async def test_usuarios_tab_has_filter_form(self, admin_client):
        """Usuarios tab includes the filter bar with search, role, and active inputs."""
        resp = await admin_client.get("/settings?tab=usuarios")
        assert resp.status_code == 200
        body = resp.text
        assert 'name="user_search"' in body
        assert 'name="user_role"' in body
        assert 'name="user_active"' in body
        assert "Filtrar" in body

    async def test_usuarios_tab_filter_form_has_hidden_tab_input(self, admin_client):
        """Filter form must preserve ?tab=usuarios on submit."""
        resp = await admin_client.get("/settings?tab=usuarios")
        assert resp.status_code == 200
        assert 'name="tab" value="usuarios"' in resp.text

    async def test_usuarios_tab_filter_preserves_search_value(self, admin_client):
        """Query param user_search re-populates the search input."""
        resp = await admin_client.get("/settings?tab=usuarios&user_search=ez")
        assert resp.status_code == 200
        assert 'value="ez"' in resp.text


class TestAgentSettingsAccess:
    """Fix 1 (Bug 12) — /settings accessible to agents; admin-only tabs hidden."""

    async def test_agent_can_access_settings_mi_cuenta(self, agent_client):
        """Agent login → GET /settings?tab=mi-cuenta → 200, muestra form cambio password."""
        resp = await agent_client.get("/settings?tab=mi-cuenta")
        assert resp.status_code == 200
        body = resp.text
        assert "Cambiar contraseña" in body
        assert 'name="current_password"' in body
        assert 'name="new_password"' in body
        assert "/me/password" in body

    async def test_agent_cannot_see_admin_tabs_in_settings(self, agent_client):
        """Agent ve Mi Cuenta tab pero NO ve Configuración del Bot / Accesos / Usuarios en el tab-bar."""
        resp = await agent_client.get("/settings")
        assert resp.status_code == 200
        body = resp.text
        assert "Mi Cuenta" in body
        # Admin-only tab labels must not appear as tab buttons for agents
        assert "Configuración del Bot" not in body
        assert "Accesos" not in body
        assert "Usuarios" not in body

    async def test_admin_still_sees_all_tabs(self, admin_client):
        """Admin ve los 4 tabs en el tab-bar."""
        resp = await admin_client.get("/settings")
        assert resp.status_code == 200
        body = resp.text
        assert "Configuración del Bot" in body
        assert "Accesos" in body
        assert "Usuarios" in body
        assert "Mi Cuenta" in body

    async def test_settings_post_toggles_still_admin_only(self, agent_client):
        """POST /settings/bot-toggle como agente → 403."""
        resp = await agent_client.post("/settings/bot-toggle")
        assert resp.status_code == 403


class TestBotTabContentIsAdminOnly:
    """A3 — el tab `bot` no tenia el guard `{% if user.role == 'admin' %}`.

    El tab-bar sí lo ocultaba, pero el `<div>` con settings_form.html se
    renderizaba igual: cualquier asesor recibia los cinco toggles y la tabla
    de settings en el HTML, y los veia entrando por /settings?tab=bot. Los
    POST estan protegidos con require_admin, asi que no podia cambiar nada,
    pero leia la configuracion del bot entera.
    """

    _MARCAS = (
        "/settings/bot-toggle",
        "/settings/bot-default-mode",
        "/settings/ic-autoreply-toggle",
        "/settings/followup-toggle",
        "Estado del Bot",
    )

    @pytest.mark.parametrize("marca", _MARCAS)
    async def test_agent_no_recibe_el_form_del_bot(self, agent_client, marca):
        resp = await agent_client.get("/settings?tab=bot")
        assert resp.status_code == 200
        assert marca not in resp.text, (
            f"{marca!r} llega al HTML de un asesor: el tab bot se renderiza sin guard"
        )

    @pytest.mark.parametrize("marca", _MARCAS)
    async def test_user_no_recibe_el_form_del_bot(self, user_client, marca):
        resp = await user_client.get("/settings?tab=bot")
        assert resp.status_code == 200
        assert marca not in resp.text

    async def test_admin_si_recibe_el_form_del_bot(self, admin_client):
        resp = await admin_client.get("/settings?tab=bot")
        assert resp.status_code == 200
        for marca in self._MARCAS:
            assert marca in resp.text, f"{marca!r} desaparecio para la admin"

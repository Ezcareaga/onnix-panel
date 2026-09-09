"""Tests for /settings 3-tab shell.

La pestaña «Configuración del Bot» se fue con el bot: no quedaba nada que
configurar, solo cinco interruptores de un motor que ya no corre.

Verifies:
  - Tab shell renders with all three tab buttons (admin)
  - Tab keys appear in URL param pattern
  - Accesos tab: contains auth-audit filter form (admin only)
  - Usuarios tab: contains users table + "Crear usuario" button (admin only)
  - Mi Cuenta tab: contains password change form (all roles)
  - Non-admin sees Mi Cuenta tab but not accesos/usuarios tab buttons
"""
import pytest


class TestSettingsTabShell:
    async def test_admin_sees_all_three_tabs(self, admin_client):
        resp = await admin_client.get("/settings")
        assert resp.status_code == 200
        body = resp.text
        assert "Configuración del Bot" not in body
        assert "Accesos" in body
        assert "Usuarios" in body
        assert "Mi Cuenta" in body

    async def test_tab_keys_present_in_template(self, admin_client):
        resp = await admin_client.get("/settings")
        body = resp.text
        for tab_key in ("accesos", "usuarios", "mi-cuenta"):
            assert tab_key in body, f"tab key '{tab_key}' not found in settings template"

    async def test_no_queda_rastro_del_tab_del_bot(self, admin_client):
        """Ni el boton, ni el contenido, ni la ruta que lo respaldaba."""
        resp = await admin_client.get("/settings")
        body = resp.text
        assert "bot-toggle" not in body
        assert "Estado del Bot" not in body
        assert "bot-default-mode" not in body

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
        """Agent ve Mi Cuenta tab pero NO ve Accesos / Usuarios en el tab-bar."""
        resp = await agent_client.get("/settings")
        assert resp.status_code == 200
        body = resp.text
        assert "Mi Cuenta" in body
        # Admin-only tab labels must not appear as tab buttons for agents
        assert "Accesos" not in body
        assert "Usuarios" not in body

    async def test_admin_still_sees_all_tabs(self, admin_client):
        """Admin ve los 3 tabs en el tab-bar."""
        resp = await admin_client.get("/settings")
        assert resp.status_code == 200
        body = resp.text
        assert "Accesos" in body
        assert "Usuarios" in body
        assert "Mi Cuenta" in body


class TestElTabDelBotNoVuelve:
    """El guard de rol del tab `bot` se volvio innecesario: no hay tab.

    Antes el `<div>` con settings_form.html se renderizaba sin
    `{% if user.role == 'admin' %}`, asi que cualquier asesor recibia los
    cinco toggles en el HTML entrando por /settings?tab=bot. Ahora la
    respuesta correcta para los tres roles es la misma: nada.
    """

    _MARCAS = (
        "/settings/bot-toggle",
        "/settings/bot-default-mode",
        "/settings/ic-autoreply-toggle",
        "/settings/followup-toggle",
        "Estado del Bot",
    )

    @pytest.mark.parametrize("marca", _MARCAS)
    async def test_ningun_rol_recibe_el_form_del_bot(
        self, admin_client, agent_client, user_client, marca,
    ):
        for nombre, cliente in (
            ("admin", admin_client), ("agent", agent_client), ("user", user_client),
        ):
            resp = await cliente.get("/settings?tab=bot")
            assert resp.status_code == 200
            assert marca not in resp.text, (
                f"{marca!r} volvio al HTML de {nombre}"
            )

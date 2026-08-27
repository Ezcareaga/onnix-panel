"""
Tests for app/routes/users.py

Covers: GET /users (admin only), POST /users (create), GET/POST edit,
        POST toggle (active/inactive), POST password change.
        M6.1 UX: HX-Trigger showToast on edit/toggle/password mutations.
"""
import json
import pytest


class TestGetUsers:
    async def test_unauthenticated_redirects(self, client):
        # /users 301-redirects to /settings?tab=usuarios; unauthenticated
        # user hits /settings which gates access → 303 to /login.
        # Either way, the final response is a redirect.
        resp = await client.get("/users")
        assert resp.status_code in (301, 303)

    async def test_user_role_gets_403(self, user_client):
        # /users now 301-redirects (require_admin fires before redirect for users)
        # Actually: require_admin fires first → 403 for non-admin
        resp = await user_client.get("/users")
        assert resp.status_code == 403

    async def test_admin_gets_301(self, admin_client):
        """GET /users now returns 301 → /settings?tab=usuarios."""
        resp = await admin_client.get("/users")
        assert resp.status_code == 301
        assert resp.headers["location"] == "/settings?tab=usuarios"

    async def test_settings_usuarios_shows_existing_users(self, admin_client):
        """Users are visible in /settings?tab=usuarios (the new canonical URL)."""
        resp = await admin_client.get("/settings?tab=usuarios")
        assert resp.status_code == 200
        assert b"ez@onnix.com.py" in resp.content

    async def test_settings_usuarios_contains_create_modal(self, admin_client):
        resp = await admin_client.get("/settings?tab=usuarios")
        assert b"Crear usuario" in resp.content


class TestCreateUser:
    async def test_creates_user_returns_200(self, admin_client):
        resp = await admin_client.post("/users", data={
            "name": "Pytest New",
            "email": "pytest_route_create@onnixtest.com",
            "password": "TestPass1234!",
            "role": "user",
        })
        assert resp.status_code == 200

    async def test_new_user_appears_in_settings_usuarios_tab(self, admin_client):
        await admin_client.post("/users", data={
            "name": "Pytest Visible",
            "email": "pytest_route_visible@onnixtest.com",
            "password": "TestPass1234!",
            "role": "user",
        })
        resp = await admin_client.get("/settings?tab=usuarios")
        assert resp.status_code == 200
        assert b"pytest_route_visible@onnixtest.com" in resp.content

    async def test_duplicate_email_shows_error(self, admin_client):
        await admin_client.post("/users", data={
            "name": "Dup One",
            "email": "pytest_route_dup@onnixtest.com",
            "password": "TestPass1234!",
            "role": "user",
        })
        resp = await admin_client.post("/users", data={
            "name": "Dup Two",
            "email": "pytest_route_dup@onnixtest.com",
            "password": "TestPass1234!",
            "role": "user",
        })
        # POST /users returns 422 on validation/creation error (Item 1 UX fix)
        assert resp.status_code == 422
        assert b"error" in resp.content.lower() or b"existe" in resp.content.lower()

    async def test_missing_fields_shows_error(self, admin_client):
        """Un mensaje por campo, no una frase que los tapa a los tres."""
        resp = await admin_client.post("/users", data={
            "name": "", "email": "", "password": "", "role": "user",
        })
        # POST /users returns 422 on validation error (Item 1 UX fix)
        assert resp.status_code == 422
        cuerpo = resp.content.decode()
        for msg in ("Nombre requerido", "Email requerido", "Contraseña requerida"):
            assert msg in cuerpo, f"falta el error del campo: {msg}"

    async def test_short_password_shows_error(self, admin_client):
        resp = await admin_client.post("/users", data={
            "name": "Short Pwd",
            "email": "pytest_route_short@onnixtest.com",
            "password": "short11",  # 7 chars, below min-12
            "role": "user",
        })
        # POST /users returns 422 on validation error (Item 1 UX fix)
        assert resp.status_code == 422
        assert b"12" in resp.content  # "12 caracteres" in error (NIST 2024 length rule)

    async def test_password_of_exactly_12_accepted(self, admin_client):
        resp = await admin_client.post("/users", data={
            "name": "Pw12 User",
            "email": "pytest_route_pw12@onnixtest.com",
            "password": "exactlytwelv",  # 12 chars
            "role": "user",
        })
        assert resp.status_code == 200
        # No "requeridos" error — creation succeeded and table returned
        assert b"requeridos" not in resp.content.lower()

    async def test_accepts_agent_role(self, admin_client):
        resp = await admin_client.post("/users", data={
            "name": "New Agent",
            "email": "pytest_route_agent_role@onnixtest.com",
            "password": "agentpass1234",
            "role": "agent",
        })
        assert resp.status_code == 200

    async def test_accepts_display_name_and_phone(self, admin_client):
        resp = await admin_client.post("/users", data={
            "name": "Full User",
            "email": "pytest_route_fullfields@onnixtest.com",
            "password": "fullfields1234",
            "role": "agent",
            "display_name": "Full",
            "phone": "+595981234567",
        })
        assert resp.status_code == 200

    async def test_user_role_gets_403(self, user_client):
        resp = await user_client.post("/users", data={
            "name": "X", "email": "x@x.com", "password": "Test1234!", "role": "user",
        })
        assert resp.status_code == 403


class TestEditUser:
    async def test_get_edit_form_returns_200(self, admin_client, db):
        # Get Ez's ID
        from app.repositories.user_repo import user_repo
        ez = await user_repo.get_by_email(db, "ez@onnix.com.py")
        resp = await admin_client.get(f"/users/{ez.id}/edit")
        assert resp.status_code == 200

    async def test_edit_form_contains_current_values(self, admin_client, db):
        from app.repositories.user_repo import user_repo
        ez = await user_repo.get_by_email(db, "ez@onnix.com.py")
        resp = await admin_client.get(f"/users/{ez.id}/edit")
        assert b"ez@onnix.com.py" in resp.content

    async def test_get_edit_nonexistent_returns_404(self, admin_client):
        resp = await admin_client.get("/users/999999/edit")
        assert resp.status_code == 404

    async def test_post_edit_updates_user(self, admin_client, db):
        from app.services.user_management_service import user_management_service
        user = await user_management_service.create_user(
            db, email="pytest_route_edit@onnixtest.com",
            password="TestPass1234!", name="Before Edit", role="user",
        )
        resp = await admin_client.post(f"/users/{user.id}/edit", data={
            "name": "After Edit",
            "email": "pytest_route_edit@onnixtest.com",
            "role": "user",
        })
        assert resp.status_code == 200
        assert b"After Edit" in resp.content

    async def test_user_role_gets_403_on_edit(self, user_client, db):
        from app.repositories.user_repo import user_repo
        ez = await user_repo.get_by_email(db, "ez@onnix.com.py")
        resp = await user_client.get(f"/users/{ez.id}/edit")
        assert resp.status_code == 403


class TestToggleActive:
    async def test_toggle_deactivates_user(self, admin_client, db):
        from app.services.user_management_service import user_management_service
        user = await user_management_service.create_user(
            db, email="pytest_route_tog@onnixtest.com",
            password="TestPass1234!", name="Toggle Me", role="user",
        )
        resp = await admin_client.post(f"/users/{user.id}/toggle")
        assert resp.status_code == 200
        assert b"Inactivo" in resp.content

    async def test_cannot_toggle_self(self, admin_client, db):
        from app.repositories.user_repo import user_repo
        ez = await user_repo.get_by_email(db, "ez@onnix.com.py")
        resp = await admin_client.post(f"/users/{ez.id}/toggle")
        assert resp.status_code == 400

    async def test_nonexistent_user_returns_404(self, admin_client):
        resp = await admin_client.post("/users/999999/toggle")
        assert resp.status_code == 404


class TestChangePassword:
    async def test_admin_changes_any_password(self, admin_client, db):
        from app.services.user_management_service import user_management_service
        user = await user_management_service.create_user(
            db, email="pytest_route_pwd@onnixtest.com",
            password="OldPassword123!", name="Pwd Change", role="user",
        )
        resp = await admin_client.post(f"/users/{user.id}/password", data={
            "password": "NewPassword456!",  # 15 chars — passes min-12
        })
        assert resp.status_code == 200

    async def test_short_password_rejected(self, admin_client, db):
        from app.services.user_management_service import user_management_service
        user = await user_management_service.create_user(
            db, email="pytest_route_short_pwd@onnixtest.com",
            password="OldPassword123!", name="Short Pwd", role="user",
        )
        resp = await admin_client.post(f"/users/{user.id}/password", data={
            "password": "short",  # below min-12
        })
        # 200 con el error adentro, NO 400.
        #
        # Este endpoint lo llama HTMX con `hx-swap="outerHTML"`, y **HTMX no
        # hace swap en una respuesta 4xx**. Con el 400, la duena apretaba
        # «Cambiar contrasena» y no pasaba absolutamente nada: ni el cambio, ni
        # un mensaje, ni una pista. El alta de usuario ya renderizaba su error
        # adentro del formulario; esta ruta era la unica que levantaba la
        # excepcion.
        #
        # Lo que importa no es el codigo sino que el rechazo se vea, asi que se
        # assertea el mensaje y no solo el 200.
        assert resp.status_code == 200
        assert "al menos 12 caracteres" in resp.text
        # Y que la contrasena NO haya cambiado, que es la mitad que un test de
        # codigo de estado no mira: un 200 podria significar «lo hice».
        import bcrypt
        await db.refresh(user)
        assert not bcrypt.checkpw(b"short", user.password_hash.encode("utf-8"))
        assert bcrypt.checkpw(b"OldPassword123!", user.password_hash.encode("utf-8"))

    async def test_user_cannot_change_others_password(self, user_client, db):
        from app.repositories.user_repo import user_repo
        ez = await user_repo.get_by_email(db, "ez@onnix.com.py")
        resp = await user_client.post(f"/users/{ez.id}/password", data={
            "password": "HackPass123!",
        })
        assert resp.status_code == 403

    async def test_nonexistent_user_returns_404(self, admin_client):
        resp = await admin_client.post("/users/999999/password", data={
            "password": "NewPass456!ValidLen",  # min 12 chars post-M6.1 UX refactor
        })
        assert resp.status_code == 404


class TestAdminSelfPasswordKeepsSession:
    """FIX 2 — admin changing their OWN password via admin route must not be logged out."""

    async def test_admin_own_password_session_survives(self, db):
        """After admin changes own password via /users/{id}/password, session stays valid."""
        import os
        from app.main import app
        from httpx import ASGITransport, AsyncClient
        import bcrypt as _bcrypt
        from sqlalchemy import text

        # Create a dedicated admin for this test (to avoid touching the real ez admin)
        known_pw = "adminself_orig1234"
        pw_hash = _bcrypt.hashpw(known_pw.encode(), _bcrypt.gensalt(rounds=4)).decode()
        uname = "pytest_adminself"
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active, username) "
            "VALUES ('pytest_adminself@onnixtest.com', 'Admin Self', 'admin', :ph, true, :un) "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true, role='admin', username=:un"
        ), {"ph": pw_hash, "un": uname})
        await db.commit()

        # Get the new admin user's id
        result = await db.execute(text(
            "SELECT id FROM users WHERE email='pytest_adminself@onnixtest.com'"
        ))
        admin_id = result.scalar_one()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            # Login as the test admin
            login_r = await c.post("/login", data={
                "email": "pytest_adminself@onnixtest.com",
                "password": known_pw,
            })
            assert login_r.status_code == 303, f"Login failed: {login_r.status_code}"

            # Admin changes their OWN password via the admin route
            change_r = await c.post(f"/users/{admin_id}/password", data={
                "password": "adminself_new1234",
            })
            assert change_r.status_code == 200, f"Password change failed: {change_r.status_code}"

            # CRITICAL: follow-up authenticated request with the SAME session must succeed.
            # Must use a protected endpoint (get_current_user enforces pw_changed_at check).
            # /dashboard requires auth and calls get_current_user — the session would be
            # invalidated (303 to /login) if pw_changed_at > issued_at and we don't refresh.
            followup_r = await c.get("/dashboard")
            assert followup_r.status_code not in (303,) or "/login" not in followup_r.headers.get("location", ""), (
                f"Admin session must survive changing own password via admin route — "
                f"got redirect to /login (session was invalidated). "
                f"status={followup_r.status_code} location={followup_r.headers.get('location', '')}"
            )
            # Must not be a login redirect — must return either 200 or some non-login redirect
            assert followup_r.status_code in (200, 301, 302), (
                f"Expected authenticated response (200/301/302), got {followup_r.status_code}"
            )

    async def test_admin_changing_other_user_still_invalidates_their_session(self, db):
        """When admin changes ANOTHER user's password, that other user's sessions are invalidated.

        This verifies the existing behavior for cross-user password change is NOT broken.
        pw_changed_at must be updated on the target, which invalidates their old sessions.
        """
        from app.services.user_management_service import user_management_service
        from app.repositories.user_repo import user_repo
        from sqlalchemy import text

        import bcrypt as _bcrypt

        known_pw = "othertarget_orig1234"
        pw_hash = _bcrypt.hashpw(known_pw.encode(), _bcrypt.gensalt(rounds=4)).decode()
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active) "
            "VALUES ('pytest_adminother@onnixtest.com', 'Other Target', 'user', :ph, true) "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true, role='user'"
        ), {"ph": pw_hash})
        await db.commit()

        target = await user_repo.get_by_email(db, "pytest_adminother@onnixtest.com")
        assert target is not None

        from datetime import datetime, timezone
        before = datetime.now(timezone.utc)
        await user_management_service.change_password(db, target.id, "adminother_new1234")
        after = datetime.now(timezone.utc)

        # pw_changed_at must be set on the target (will invalidate their sessions)
        result = await db.execute(text(
            "SELECT pw_changed_at FROM users WHERE email='pytest_adminother@onnixtest.com'"
        ))
        row = result.fetchone()
        assert row is not None
        assert row[0] is not None, "pw_changed_at must be set on target after admin password change"


class TestCreateUserFormResponse:
    """Item 1 — POST /users returns 200 on success, 422 on validation error."""

    async def test_create_user_form_returns_200_on_success(self, admin_client):
        resp = await admin_client.post("/users", data={
            "name": "UX Fix User",
            "email": "pytest_uxfix_s1@onnixtest.com",
            "password": "UxFixPass1234!",
            "role": "agent",
        })
        assert resp.status_code == 200

    async def test_create_user_form_returns_422_on_duplicate_email(self, admin_client):
        # Create once
        await admin_client.post("/users", data={
            "name": "Dup Base",
            "email": "pytest_uxfix_d1@onnixtest.com",
            "password": "DupBasePass1234!",
            "role": "user",
        })
        # Try duplicate
        resp = await admin_client.post("/users", data={
            "name": "Dup Again",
            "email": "pytest_uxfix_d1@onnixtest.com",
            "password": "DupAgainPass1234!",
            "role": "user",
        })
        assert resp.status_code == 422

    async def test_create_user_form_returns_422_on_short_password(self, admin_client):
        resp = await admin_client.post("/users", data={
            "name": "Short Pw",
            "email": "pytest_uxfix_sp@onnixtest.com",
            "password": "short",
            "role": "user",
        })
        assert resp.status_code == 422

    async def test_success_response_has_hx_trigger_header(self, admin_client):
        resp = await admin_client.post("/users", data={
            "name": "Trigger User",
            "email": "pytest_uxfix_t1@onnixtest.com",
            "password": "TriggerPass1234!",
            "role": "agent",
        })
        assert resp.status_code == 200
        assert "HX-Trigger" in resp.headers

    async def test_error_response_has_no_hx_trigger_header(self, admin_client):
        resp = await admin_client.post("/users", data={
            "name": "", "email": "", "password": "", "role": "user",
        })
        assert resp.status_code == 422
        assert "HX-Trigger" not in resp.headers



class TestCreateUserFieldErrors:
    """El error de alta aparece al lado del campo, y dentro del modal.

    Patron rescatado de users_create_agent.html, que era el unico formulario
    del panel que lo hacia bien y que se borro en el carril D.

    La otra mitad del bug era donde caia la respuesta: el 422 se swapeaba en
    #settings-users-table, que esta debajo del <dialog> abierto — el usuario
    apretaba Guardar y no veia pasar nada. Por eso el test mira tambien el
    header de retarget: sin el, el mensaje existe y nadie lo lee.
    """

    async def test_el_error_se_pinta_dentro_del_formulario(self, admin_client):
        resp = await admin_client.post("/users", data={
            "name": "Sin Telefono", "email": "pytest_fielderr_ph@onnixtest.com",
            "password": "TestPass1234!", "role": "agent", "phone": "notaphone",
        })
        assert resp.status_code == 422
        assert resp.headers.get("HX-Retarget") == "#create-user-form"
        assert resp.headers.get("HX-Reswap") == "outerHTML"

    async def test_el_mensaje_queda_atado_a_su_campo(self, admin_client):
        resp = await admin_client.post("/users", data={
            "name": "Bad Phone", "email": "pytest_fielderr_ph2@onnixtest.com",
            "password": "TestPass1234!", "role": "agent", "phone": "notaphone",
        })
        cuerpo = resp.content.decode()
        assert 'aria-describedby="nu-phone-error"' in cuerpo
        assert 'id="nu-phone-error"' in cuerpo
        # y ningun otro campo queda marcado como invalido
        assert 'aria-describedby="nu-email-error"' not in cuerpo
        assert 'aria-describedby="nu-name-error"' not in cuerpo

    async def test_no_se_pierde_lo_ya_tipeado(self, admin_client):
        """Volver a escribir cinco campos por un telefono mal puesto es
        exactamente lo que este formulario no debe hacer."""
        resp = await admin_client.post("/users", data={
            "name": "Nombre Tipeado", "display_name": "Tipeado",
            "email": "pytest_fielderr_keep@onnixtest.com",
            "password": "TestPass1234!", "role": "admin", "phone": "notaphone",
        })
        cuerpo = resp.content.decode()
        assert 'value="Nombre Tipeado"' in cuerpo
        assert 'value="Tipeado"' in cuerpo
        assert 'value="pytest_fielderr_keep@onnixtest.com"' in cuerpo
        assert '<option value="admin" selected>' in cuerpo

    async def test_la_contrasena_no_vuelve_en_el_html(self, admin_client):
        """Reescribirla es el precio de no mandarla de vuelta al navegador."""
        resp = await admin_client.post("/users", data={
            "name": "Pwd Echo", "email": "pytest_fielderr_pwd@onnixtest.com",
            "password": "TestPass1234!", "role": "agent", "phone": "notaphone",
        })
        assert b"TestPass1234!" not in resp.content


class TestUsersOrderedByCreatedAt:
    """Item 2 — newest user appears first (ORDER BY created_at DESC)."""

    async def test_users_ordered_by_created_at_desc(self, db):
        from app.services.user_management_service import user_management_service
        await user_management_service.create_user(
            db, email="pytest_ord_a@onnixtest.com",
            password="OrderFirst1234!", name="Order First", role="user",
        )
        await user_management_service.create_user(
            db, email="pytest_ord_b@onnixtest.com",
            password="OrderSecond1234!", name="Order Second", role="user",
        )
        users = await user_management_service.get_all(db)
        emails = [u.email for u in users]
        assert emails.index("pytest_ord_b@onnixtest.com") < emails.index("pytest_ord_a@onnixtest.com"), (
            "Newest user should appear before older user"
        )


class TestUsersFilters:
    """Items 4 — filter by role, search, and active state."""

    async def test_users_filter_by_role(self, db):
        from app.services.user_management_service import user_management_service
        await user_management_service.create_user(
            db, email="pytest_filt_ag@onnixtest.com",
            password="FilterAgent1234!", name="Filter Agent", role="agent",
        )
        await user_management_service.create_user(
            db, email="pytest_filt_us@onnixtest.com",
            password="FilterUser1234!", name="Filter User", role="user",
        )
        agents = await user_management_service.get_all(db, role="agent")
        assert all(u.role == "agent" for u in agents)
        users_only = await user_management_service.get_all(db, role="user")
        assert all(u.role == "user" for u in users_only)

    async def test_users_filter_by_search_name(self, db):
        from app.services.user_management_service import user_management_service
        await user_management_service.create_user(
            db, email="pytest_srch_nm@onnixtest.com",
            password="SearchTarget1234!", name="UniqSearchableName", role="user",
        )
        results = await user_management_service.get_all(db, search="uniqsearchablename")
        emails = [u.email for u in results]
        assert "pytest_srch_nm@onnixtest.com" in emails

    async def test_users_filter_by_search_email(self, db):
        from app.services.user_management_service import user_management_service
        await user_management_service.create_user(
            db, email="pytest_srch_em@onnixtest.com",
            password="SearchEmail1234!", name="Email Search Two", role="user",
        )
        results = await user_management_service.get_all(db, search="pytest_srch_em")
        assert any(u.email == "pytest_srch_em@onnixtest.com" for u in results)

    async def test_users_filter_active_default_true(self, db):
        from app.services.user_management_service import user_management_service
        user = await user_management_service.create_user(
            db, email="pytest_inact_f@onnixtest.com",
            password="InactiveFilter1234!", name="Inactive Filter Two", role="user",
        )
        # Deactivate the user
        await user_management_service.toggle_active(db, user.id)
        # Default get_all (active=True) should not include this user
        results = await user_management_service.get_all(db)
        emails = [u.email for u in results]
        assert "pytest_inact_f@onnixtest.com" not in emails

    async def test_settings_usuarios_filter_by_role_query_param(self, admin_client):
        resp = await admin_client.get("/settings?tab=usuarios&user_role=admin")
        assert resp.status_code == 200

    async def test_settings_usuarios_filter_by_search_query_param(self, admin_client):
        resp = await admin_client.get("/settings?tab=usuarios&user_search=ez")
        assert resp.status_code == 200
        assert b"ez@onnix.com.py" in resp.content


class TestUsersToastHeaders:
    """M6.1 UX — HX-Trigger showToast on all user mutation endpoints."""

    async def _upsert_test_user(self, db, email: str, name: str) -> int:
        """Insert-or-update a test user; return its id. Safe for consecutive runs."""
        import bcrypt
        from sqlalchemy import text
        ph = bcrypt.hashpw(b"ToastTest1234!", bcrypt.gensalt(rounds=4)).decode()
        uname = email.split("@")[0]
        row = (await db.execute(
            text("""
                INSERT INTO users (email, password_hash, name, role, is_active, username)
                VALUES (:email, :ph, :name, 'user', true, :uname)
                ON CONFLICT (email) DO UPDATE
                    SET password_hash = EXCLUDED.password_hash,
                        name = EXCLUDED.name,
                        is_active = true
                RETURNING id
            """),
            {"email": email, "ph": ph, "name": name, "uname": uname},
        )).scalar_one()
        await db.commit()
        return row

    async def test_edit_user_returns_show_toast_header(self, admin_client, db):
        uid = await self._upsert_test_user(db, "pytest_toast_edit@onnixtest.com", "Toast Edit")
        resp = await admin_client.post(f"/users/{uid}/edit", data={
            "name": "Toast Edited",
            "email": "pytest_toast_edit@onnixtest.com",
            "role": "user",
        })
        assert resp.status_code == 200
        hx = resp.headers.get("hx-trigger", "")
        assert hx, "HX-Trigger must be present on edit success"
        data = json.loads(hx)
        assert data["showToast"]["type"] == "success"
        assert "actualizado" in data["showToast"]["message"].lower()

    async def test_toggle_user_returns_show_toast_header(self, admin_client, db):
        uid = await self._upsert_test_user(db, "pytest_toast_tog@onnixtest.com", "Toast Toggle")
        resp = await admin_client.post(f"/users/{uid}/toggle")
        assert resp.status_code == 200
        hx = resp.headers.get("hx-trigger", "")
        assert hx, "HX-Trigger must be present on toggle success"
        data = json.loads(hx)
        assert data["showToast"]["type"] == "success"
        assert "usuario" in data["showToast"]["message"].lower()

    async def test_change_password_returns_show_toast_header(self, admin_client, db):
        uid = await self._upsert_test_user(db, "pytest_toast_pwd@onnixtest.com", "Toast Pwd")
        resp = await admin_client.post(f"/users/{uid}/password", data={
            "password": "NewToastPwd1234!",
        })
        assert resp.status_code == 200
        hx = resp.headers.get("hx-trigger", "")
        assert hx, "HX-Trigger must be present on password change success"
        data = json.loads(hx)
        assert data["showToast"]["type"] == "success"
        assert "contraseña" in data["showToast"]["message"].lower()


class TestEditFormPreservesAgentRole:
    """El <select name="role"> del form de edicion solo ofrecia user y admin.

    Con target.role == 'agent' ninguna option queda selected, el navegador
    manda la primera (user), y al guardar el asesor pierde el rol y el acceso
    a /leads. Silencioso, sin aviso.
    """

    async def test_edit_form_offers_agent_option(self, admin_client, db):
        from app.services.user_management_service import user_management_service

        agent = await user_management_service.create_user(
            db, email="pytest_agent_role@onnixtest.com",
            password="TestPass1234!", name="Asesor Pytest", role="agent",
        )
        resp = await admin_client.get(f"/users/{agent.id}/edit")
        assert resp.status_code == 200
        html = resp.text
        assert 'value="agent"' in html, (
            "el select de rol no ofrece 'agent': editar un asesor lo degrada a user"
        )

    async def test_agent_option_is_selected_for_an_agent(self, admin_client, db):
        from app.services.user_management_service import user_management_service

        agent = await user_management_service.create_user(
            db, email="pytest_agent_sel@onnixtest.com",
            password="TestPass1234!", name="Asesor Selected", role="agent",
        )
        resp = await admin_client.get(f"/users/{agent.id}/edit")
        html = resp.text
        # La option de agent tiene que venir marcada, si no el submit manda 'user'.
        idx = html.find('value="agent"')
        assert idx != -1
        tail = html[idx:idx + 120]
        assert "selected" in tail, (
            "la option 'agent' existe pero no viene selected para un usuario agent"
        )

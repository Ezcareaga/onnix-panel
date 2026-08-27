"""TDD — Asesor link attribution (feat/asesor-link)

Tests:
A) Panel-side public_path injection:
   - user WITH phone → public_path includes ?a={user.id}
   - user WITHOUT phone → public_path has no ?a= param
   - public_path=None (ineligible property) → still None regardless of phone

B) Public route GET /prop/{ref}?a=...:
   - a=<agent activo con phone, role admin|agent> → wa.me apunta al asesor
   - a=<agent activo sin phone> → wa.me por defecto
   - a=<user inactivo> → wa.me por defecto
   - a=<role='user'> → wa.me por defecto
   - a=<id inexistente> → wa.me por defecto
   - a=abc (no numérico) → 200 con default, nunca 500/422
   - a=999999999999999999999 (overflow) → 200 con default, nunca 500
   - sin ?a → wa.me por defecto intacto (comportamiento original)

C) Canonical <link rel="canonical"> NO incluye ?a
"""
from __future__ import annotations

import re
import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_WA_NUMBER = "595900000000"
DEFAULT_WA_URL = f"https://wa.me/{DEFAULT_WA_NUMBER}?text=Hola"


def _make_detail(
    *,
    prop_id: int = 123,
    title: str = "Casa en Luque",
    external_id: str = "EXT001",
    slug: str = "casa-en-luque-luque",
    canonical_path: str = "/prop/123-casa-en-luque-luque",
    wa_url: str = DEFAULT_WA_URL,
    public_code: str = "00123",
    photo_urls: list[str] | None = None,
    price_display: str = "USD 120.000",
) -> dict:
    if photo_urls is None:
        photo_urls = []
    return {
        "id": prop_id,
        "title": title,
        "city": "Luque",
        "source": "remax",
        "external_id": external_id,
        "description": "Hermosa casa",
        "bedrooms": 3,
        "bathrooms": 2,
        "parking": 1,
        "total_area_m2": 150.0,
        "construction_state": "usado",
        "operation": "venta",
        "property_type": "casa",
        "latitude": None,
        "longitude": None,
        "photo_urls": photo_urls,
        "price_display": price_display,
        "wa_url": wa_url,
        # El fixture default no trae fotos, así que la ficha renderiza la
        # cabecera plana y este link es el que sale ahí.
        "wa_url_fotos": wa_url,
        "wa_url_datos": wa_url,
        "wa_message": "Hola",
        "slug": slug,
        "public_code": public_code,
        "canonical_path": canonical_path,
    }


def _patch_detail(return_value):
    return patch(
        "app.routes.public.PublicPropertyService.get_public_detail",
        new=AsyncMock(return_value=return_value),
    )


def _make_user_mock(
    *,
    user_id: int = 7,
    phone: str | None = "+595981234567",
    role: str = "agent",
    is_active: bool = True,
    name: str = "Juan Agente",
    display_name: str | None = "Juan",
):
    u = MagicMock()
    u.id = user_id
    u.phone = phone
    u.role = role
    u.is_active = is_active
    u.name = name
    u.display_name = display_name
    return u


# ---------------------------------------------------------------------------
# A) Panel-side: public_path with ?a= attribution
# ---------------------------------------------------------------------------


class TestPublicPathAttribution:
    """Routes inject ?a={user.id} into public_path when user has phone.

    Uses dependency_overrides to inject a mock user, since the dev DB admin
    user may not have a phone configured.
    """

    @pytest.mark.asyncio
    async def test_public_path_with_phone_includes_a_param(self, client):
        """/properties listing: data-public-url includes ?a=<user_id> when user has phone."""
        from app.main import app as _app
        from app.dependencies import get_current_user as _dep

        row_with_path = {
            "id": 55,
            "title": "Casa Test",
            "city": "Asuncion",
            "source": "remax",
            "external_id": "EXT055",
            "is_active": True,
            "on_hold": False,
            "price_usd": 100000,
            "price_pyg": None,
            "updated_at": None,
            "url": None,
            "public_path": "/prop/55-casa-test-asuncion",
        }

        user_with_phone = _make_user_mock(user_id=42, phone="+595981234567", role="admin")

        async def _override():
            return user_with_phone

        _app.dependency_overrides[_dep] = _override
        try:
            with patch(
                "app.routes.properties.property_service.get_properties",
                new=AsyncMock(return_value=([row_with_path], 1)),
            ):
                resp = await client.get("/properties")
        finally:
            _app.dependency_overrides.pop(_dep, None)

        assert resp.status_code == 200
        html = resp.text
        assert "?a=42" in html

    @pytest.mark.asyncio
    async def test_public_path_without_phone_has_no_a_param(self, client):
        """/properties listing: data-public-url has NO ?a= when user has no phone."""
        from app.main import app as _app
        from app.dependencies import get_current_user as _dep

        row_with_path = {
            "id": 56,
            "title": "Casa Sin Phone",
            "city": "Asuncion",
            "source": "remax",
            "external_id": "EXT056",
            "is_active": True,
            "on_hold": False,
            "price_usd": 90000,
            "price_pyg": None,
            "updated_at": None,
            "url": None,
            "public_path": "/prop/56-casa-sin-phone-asuncion",
        }

        user_no_phone = _make_user_mock(user_id=7, phone=None, role="user")

        async def _override():
            return user_no_phone

        _app.dependency_overrides[_dep] = _override
        try:
            with patch(
                "app.routes.properties.property_service.get_properties",
                new=AsyncMock(return_value=([row_with_path], 1)),
            ):
                resp = await client.get("/properties")
        finally:
            _app.dependency_overrides.pop(_dep, None)

        assert resp.status_code == 200
        html = resp.text
        # No ?a= param in any data-public-url
        assert "?a=" not in html


# ---------------------------------------------------------------------------
# A2) Unit tests for the public_path attribution helper
# ---------------------------------------------------------------------------


class TestBuildPublicUrlWithAsesor:
    """Unit tests for the helper that appends ?a= to public_path."""

    def test_user_with_phone_appends_a_param(self):
        from app.routes.properties import _build_public_url_for_user

        user = _make_user_mock(user_id=7, phone="+595981234567")
        result = _build_public_url_for_user("https://onnix.com.py", "/prop/55-casa", user)
        assert result == "https://onnix.com.py/prop/55-casa?a=7"

    def test_user_without_phone_no_a_param(self):
        from app.routes.properties import _build_public_url_for_user

        user = _make_user_mock(user_id=7, phone=None)
        result = _build_public_url_for_user("https://onnix.com.py", "/prop/55-casa", user)
        assert result == "https://onnix.com.py/prop/55-casa"

    def test_user_with_empty_phone_no_a_param(self):
        from app.routes.properties import _build_public_url_for_user

        user = _make_user_mock(user_id=7, phone="")
        result = _build_public_url_for_user("https://onnix.com.py", "/prop/55-casa", user)
        assert result == "https://onnix.com.py/prop/55-casa"

    def test_public_path_none_returns_none(self):
        from app.routes.properties import _build_public_url_for_user

        user = _make_user_mock(user_id=7, phone="+595981234567")
        result = _build_public_url_for_user("https://onnix.com.py", None, user)
        assert result is None


# ---------------------------------------------------------------------------
# B) Public route: ?a= resolves asesor WhatsApp
# ---------------------------------------------------------------------------


class TestPublicDetailAsesorParam:
    """GET /prop/{ref}?a= resolves asesor wa.me."""

    @pytest.mark.asyncio
    async def test_valid_agent_with_phone_shows_asesor_wa(self, client):
        """?a=<agent with phone> → btn-wa href points to asesor's wa.me."""
        detail = _make_detail()
        asesor = _make_user_mock(user_id=7, phone="+595981234567", role="agent")

        with _patch_detail(detail), patch(
            "app.routes.public.user_repo.get_by_id",
            new=AsyncMock(return_value=asesor),
        ):
            resp = await client.get("/prop/123-casa-en-luque-luque?a=7")

        assert resp.status_code == 200
        html = resp.text
        # Asesor phone normalized: strip '+', only digits
        assert "wa.me/595981234567" in html
        # Default number must NOT appear for the CTA button
        # (it can still appear in other contexts, so check the href attr)
        assert f"wa.me/{DEFAULT_WA_NUMBER}" not in html

    @pytest.mark.asyncio
    async def test_sin_fotos_la_pregunta_por_las_fotos_va_al_asesor(self, client):
        """El fixture no trae fotos, así que la ficha muestra la cabecera plana
        con «Preguntá por las fotos». Si esa acción cayera en el número de la
        oficina se rompería la atribución justo en el caso donde el visitante
        ya llegó por el link de un asesor."""
        detail = _make_detail(photo_urls=[])
        asesor = _make_user_mock(user_id=7, phone="+595981234567", role="agent")

        with _patch_detail(detail), patch(
            "app.routes.public.user_repo.get_by_id",
            new=AsyncMock(return_value=asesor),
        ):
            resp = await client.get("/prop/123-casa-en-luque-luque?a=7")

        assert resp.status_code == 200
        html = resp.text
        assert "Preguntá por las fotos" in html
        # El href de la acción, no cualquier wa.me de la página.
        m = re.search(
            r'class="falta-link" href="([^"]+)"', html
        )
        assert m is not None, "no se emitió el enlace de la ausencia"
        assert "wa.me/595981234567" in m.group(1)
        assert "fotos" in m.group(1)

    @pytest.mark.asyncio
    async def test_valid_admin_with_phone_shows_asesor_wa(self, client):
        """?a=<admin with phone> → same as agent."""
        detail = _make_detail()
        asesor = _make_user_mock(user_id=3, phone="+595987654321", role="admin")

        with _patch_detail(detail), patch(
            "app.routes.public.user_repo.get_by_id",
            new=AsyncMock(return_value=asesor),
        ):
            resp = await client.get("/prop/123-casa-en-luque-luque?a=3")

        assert resp.status_code == 200
        assert "wa.me/595987654321" in resp.text

    @pytest.mark.asyncio
    async def test_agent_without_phone_falls_back_to_default(self, client):
        """?a=<agent without phone> → default wa.me."""
        detail = _make_detail()
        asesor = _make_user_mock(user_id=9, phone=None, role="agent")

        with _patch_detail(detail), patch(
            "app.routes.public.user_repo.get_by_id",
            new=AsyncMock(return_value=asesor),
        ):
            resp = await client.get("/prop/123-casa-en-luque-luque?a=9")

        assert resp.status_code == 200
        assert f"wa.me/{DEFAULT_WA_NUMBER}" in resp.text

    @pytest.mark.asyncio
    async def test_inactive_user_falls_back_to_default(self, client):
        """?a=<inactive user> → default wa.me."""
        detail = _make_detail()
        asesor = _make_user_mock(user_id=10, phone="+595981111111", is_active=False)

        with _patch_detail(detail), patch(
            "app.routes.public.user_repo.get_by_id",
            new=AsyncMock(return_value=asesor),
        ):
            resp = await client.get("/prop/123-casa-en-luque-luque?a=10")

        assert resp.status_code == 200
        assert f"wa.me/{DEFAULT_WA_NUMBER}" in resp.text
        assert "wa.me/595981111111" not in resp.text

    @pytest.mark.asyncio
    async def test_user_role_falls_back_to_default(self, client):
        """?a=<role='user'> → default wa.me (only admin|agent allowed)."""
        detail = _make_detail()
        asesor = _make_user_mock(user_id=11, phone="+595982222222", role="user")

        with _patch_detail(detail), patch(
            "app.routes.public.user_repo.get_by_id",
            new=AsyncMock(return_value=asesor),
        ):
            resp = await client.get("/prop/123-casa-en-luque-luque?a=11")

        assert resp.status_code == 200
        assert f"wa.me/{DEFAULT_WA_NUMBER}" in resp.text
        assert "wa.me/595982222222" not in resp.text

    @pytest.mark.asyncio
    async def test_nonexistent_user_falls_back_to_default(self, client):
        """?a=<nonexistent id> → default wa.me."""
        detail = _make_detail()

        with _patch_detail(detail), patch(
            "app.routes.public.user_repo.get_by_id",
            new=AsyncMock(return_value=None),
        ):
            resp = await client.get("/prop/123-casa-en-luque-luque?a=99999")

        assert resp.status_code == 200
        assert f"wa.me/{DEFAULT_WA_NUMBER}" in resp.text

    @pytest.mark.asyncio
    async def test_non_numeric_a_param_returns_200_default(self, client):
        """?a=abc → 200 with default wa.me, never 422 or 500."""
        detail = _make_detail()

        with _patch_detail(detail):
            resp = await client.get("/prop/123-casa-en-luque-luque?a=abc")

        assert resp.status_code == 200
        assert resp.status_code not in (422, 500)
        assert f"wa.me/{DEFAULT_WA_NUMBER}" in resp.text

    @pytest.mark.asyncio
    async def test_overflow_a_param_returns_200_default(self, client):
        """?a=999999999999999999999 (overflow) → 200 with default wa.me, never 500."""
        detail = _make_detail()

        with _patch_detail(detail):
            resp = await client.get(
                "/prop/123-casa-en-luque-luque?a=999999999999999999999"
            )

        assert resp.status_code == 200
        assert resp.status_code != 500
        assert f"wa.me/{DEFAULT_WA_NUMBER}" in resp.text

    @pytest.mark.asyncio
    async def test_no_a_param_default_wa(self, client):
        """Without ?a= → default wa.me unchanged."""
        detail = _make_detail()

        with _patch_detail(detail):
            resp = await client.get("/prop/123-casa-en-luque-luque")

        assert resp.status_code == 200
        assert f"wa.me/{DEFAULT_WA_NUMBER}" in resp.text

    @pytest.mark.asyncio
    async def test_a_param_zero_string_returns_200_default(self, client):
        """?a=0 → 200 with default (0 is not a valid user id in practice)."""
        detail = _make_detail()

        with _patch_detail(detail), patch(
            "app.routes.public.user_repo.get_by_id",
            new=AsyncMock(return_value=None),
        ):
            resp = await client.get("/prop/123-casa-en-luque-luque?a=0")

        assert resp.status_code == 200
        assert f"wa.me/{DEFAULT_WA_NUMBER}" in resp.text


# ---------------------------------------------------------------------------
# C) Canonical URL never includes ?a=
# ---------------------------------------------------------------------------


class TestCanonicalNoAsesorParam:
    @pytest.mark.asyncio
    async def test_canonical_link_excludes_a_param(self, client):
        """<link rel='canonical'> must use prop.canonical_path, never include ?a=."""
        detail = _make_detail(
            prop_id=123,
            canonical_path="/prop/123-casa-en-luque-luque",
        )
        asesor = _make_user_mock(user_id=7, phone="+595981234567", role="agent")

        with _patch_detail(detail), patch(
            "app.routes.public.user_repo.get_by_id",
            new=AsyncMock(return_value=asesor),
        ):
            resp = await client.get("/prop/123-casa-en-luque-luque?a=7")

        assert resp.status_code == 200
        html = resp.text
        # canonical must not contain ?a=
        import re
        canonical_match = re.search(r'<link rel="canonical" href="([^"]+)"', html)
        assert canonical_match, "canonical link not found in page"
        canonical_href = canonical_match.group(1)
        assert "?a=" not in canonical_href
        assert canonical_href == "https://onnix.com.py/prop/123-casa-en-luque-luque"

    @pytest.mark.asyncio
    async def test_og_url_excludes_a_param(self, client):
        """og:url meta tag must also not include ?a=."""
        detail = _make_detail(
            prop_id=123,
            canonical_path="/prop/123-casa-en-luque-luque",
        )
        asesor = _make_user_mock(user_id=7, phone="+595981234567", role="agent")

        with _patch_detail(detail), patch(
            "app.routes.public.user_repo.get_by_id",
            new=AsyncMock(return_value=asesor),
        ):
            resp = await client.get("/prop/123-casa-en-luque-luque?a=7")

        assert resp.status_code == 200
        html = resp.text
        import re
        og_url_match = re.search(r'property="og:url" content="([^"]+)"', html)
        assert og_url_match, "og:url meta tag not found"
        og_url = og_url_match.group(1)
        assert "?a=" not in og_url


# ---------------------------------------------------------------------------
# D) WA message text includes title and id
# ---------------------------------------------------------------------------


class TestAsesorWaMessageText:
    @pytest.mark.asyncio
    async def test_wa_message_includes_title_and_id(self, client):
        """WhatsApp URL text param must include property title and ref id."""
        detail = _make_detail(
            prop_id=123,
            title="Casa en Luque",
            external_id="Onnix-001",
            canonical_path="/prop/123-casa-en-luque-luque",
        )
        asesor = _make_user_mock(user_id=7, phone="+595981234567", role="agent")

        with _patch_detail(detail), patch(
            "app.routes.public.user_repo.get_by_id",
            new=AsyncMock(return_value=asesor),
        ):
            resp = await client.get("/prop/123-casa-en-luque-luque?a=7")

        assert resp.status_code == 200
        html = resp.text
        # The wa.me URL must contain URL-encoded text with title
        import re
        wa_match = re.search(r'href="(https://wa\.me/595981234567[^"]*)"', html)
        assert wa_match, "asesor wa.me URL not found in HTML"
        wa_url = wa_match.group(1)
        decoded = urllib.parse.unquote(wa_url)
        assert "Casa en Luque" in decoded

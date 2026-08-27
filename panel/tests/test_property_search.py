"""
Tests for Task 4: Property search API + property_id plumbing through contact stack.

Phase: feat/property-association

Covers:
  - GET /api/properties/search?q= returns JSON list
  - Search by partial external_id
  - Search by partial title
  - Short query (< 2 chars) returns empty list
  - Max 6 results cap
  - create_contact with valid property_id saves property_id
  - create_contact with invalid/inactive property_id raises ValueError
  - update_contact with property_id saves it
"""
import pytest
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Real DB properties used across tests (active properties from onnix_dev).
# These are stable rows — external_id won't change because we never modify
# production-sourced data in the dev DB.
# ---------------------------------------------------------------------------

# Active property with known external_id prefix
_ACTIVE_PROPERTY_ID = 544672       # external_id='143026190-9', title='ALQUILO DEPARTAMENTO AMOBLADO'
_ACTIVE_EXTERNAL_ID = "143026190-9"

# Property used for title-search test: unique enough title, small result set (3 rows),
# all returned within the 6-result cap.
_TITLE_SEARCH_PROPERTY_ID = 13708  # title='PROPIEDAD DE 10 HECTAREAS EN CERRITO'
_ACTIVE_TITLE_FRAGMENT = "PROPIEDAD DE 10 HECTAREAS EN CERRITO"

# Inactive property id (is_active=FALSE)
_INACTIVE_PROPERTY_ID = 518024


# ===========================================================================
# GET /api/properties/search
# ===========================================================================

class TestPropertySearchEndpoint:
    """Tests for GET /api/properties/search?q=<query>."""

    async def test_unauthenticated_redirects(self, client):
        resp = await client.get("/api/properties/search?q=alquilo")
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    async def test_search_by_external_id(self, admin_client):
        """Partial external_id match returns the property in results."""
        # '143026' is a unique prefix of _ACTIVE_EXTERNAL_ID
        resp = await admin_client.get("/api/properties/search?q=143026190")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        ids = [item["id"] for item in data]
        assert _ACTIVE_PROPERTY_ID in ids

    async def test_search_by_title(self, admin_client):
        """Partial title match returns the property in results."""
        resp = await admin_client.get(f"/api/properties/search?q={_ACTIVE_TITLE_FRAGMENT}")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        ids = [item["id"] for item in data]
        assert _TITLE_SEARCH_PROPERTY_ID in ids

    async def test_search_min_length_empty_string(self, admin_client):
        """Empty q returns [] without hitting DB."""
        resp = await admin_client.get("/api/properties/search?q=")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_search_min_length_single_char(self, admin_client):
        """Single char q returns []."""
        resp = await admin_client.get("/api/properties/search?q=a")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_search_max_results(self, admin_client):
        """Search that matches many rows returns at most 6 results."""
        # 'a' is too short — use a common fragment that matches lots of rows
        resp = await admin_client.get("/api/properties/search?q=casa")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) <= 6

    async def test_search_result_schema(self, admin_client):
        """Each result dict has the required keys."""
        resp = await admin_client.get("/api/properties/search?q=143026190")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        item = data[0]
        for key in ("id", "external_id", "title", "city", "neighborhood", "price_usd"):
            assert key in item, f"Missing key: {key}"

    async def test_inactive_properties_excluded(self, admin_client):
        """is_active=FALSE properties never appear in results."""
        # Search using inactive property's id that should not match (we search by external_id)
        resp = await admin_client.get("/api/properties/search?q=34747")
        assert resp.status_code == 200
        data = resp.json()
        ids = [item["id"] for item in data]
        assert _INACTIVE_PROPERTY_ID not in ids


# ===========================================================================
# ContactService: create_contact with property_id
# ===========================================================================

class TestCreateContactWithPropertyId:
    """Tests for ContactService.create_contact() property_id parameter."""

    async def test_create_contact_with_valid_property_id(self, db):
        """create_contact with a valid active property_id persists property_id."""
        from app.services.contact_service import contact_service
        from app.repositories.contact_repo import contact_repo

        phone = "+595981700101"
        contact, error = await contact_service.create_contact(
            db,
            name="Test PropId Valid",
            phone=phone,
            email=None,
            status="new",
            operacion=None,
            zona=None,
            presupuesto_raw="",
            dormitorios_raw="",

            user_id=1,
            user_email="test@onnixtest.com",
            user_role="admin",
            property_id=_ACTIVE_PROPERTY_ID,
        )
        assert error is None, f"Expected no error, got: {error}"
        assert contact is not None
        assert contact.property_id == _ACTIVE_PROPERTY_ID

    async def test_create_contact_with_inactive_property_id_raises(self, db):
        """create_contact with an inactive property_id raises ValueError."""
        from app.services.contact_service import contact_service

        phone = "+595981700102"
        with pytest.raises(ValueError, match="not found or inactive"):
            await contact_service.create_contact(
                db,
                name="Test PropId Inactive",
                phone=phone,
                email=None,
                status="new",
                operacion=None,
                zona=None,
                presupuesto_raw="",
                dormitorios_raw="",
    
                user_id=1,
                user_email="test@onnixtest.com",
                user_role="admin",
                property_id=_INACTIVE_PROPERTY_ID,
            )

    async def test_create_contact_with_nonexistent_property_id_raises(self, db):
        """create_contact with a nonexistent property_id raises ValueError."""
        from app.services.contact_service import contact_service

        phone = "+595981700103"
        with pytest.raises(ValueError, match="not found or inactive"):
            await contact_service.create_contact(
                db,
                name="Test PropId Nonexist",
                phone=phone,
                email=None,
                status="new",
                operacion=None,
                zona=None,
                presupuesto_raw="",
                dormitorios_raw="",
    
                user_id=1,
                user_email="test@onnixtest.com",
                user_role="admin",
                property_id=99999999,
            )

    async def test_create_contact_without_property_id_still_works(self, db):
        """property_id=None (default) creates contact normally."""
        from app.services.contact_service import contact_service

        phone = "+595981700104"
        contact, error = await contact_service.create_contact(
            db,
            name="Test No PropId",
            phone=phone,
            email=None,
            status="new",
            operacion=None,
            zona=None,
            presupuesto_raw="",
            dormitorios_raw="",

            user_id=1,
            user_email="test@onnixtest.com",
            user_role="admin",
        )
        assert error is None
        assert contact is not None
        assert contact.property_id is None


# ===========================================================================
# ContactService: update_contact with property_id
# ===========================================================================

class TestUpdateContactPropertyId:
    """Tests for ContactService.update_contact() property_id parameter."""

    async def _create_base_contact(self, db, phone: str):
        """Insert a minimal contact directly via ORM for update tests."""
        from app.models.contact import Contact

        c = Contact(
            name="Update PropId Test",
            phone=phone,
            phone_normalized=phone,
            source="manual",
            status="new",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()
        return c

    async def test_update_contact_property_id(self, db):
        """update_contact with valid property_id persists the association."""
        from app.services.contact_service import contact_service
        from app.repositories.contact_repo import contact_repo

        phone = "+595981700201"
        contact = await self._create_base_contact(db, phone)
        await db.commit()

        ok, error, has_changes = await contact_service.update_contact(
            db,
            contact_id=contact.id,
            name=None,
            phone=None,
            email=None,
            operacion=None,
            zona=None,
            presupuesto_raw="",
            dormitorios_raw="",

            user_id=1,
            user_email="test@onnixtest.com",
            property_id=_ACTIVE_PROPERTY_ID,
        )
        assert ok is True, f"Expected ok=True, got error: {error}"

        updated = await contact_repo.get_by_id(db, contact.id)
        assert updated is not None
        assert updated.property_id == _ACTIVE_PROPERTY_ID

    async def test_update_contact_inactive_property_id_raises(self, db):
        """update_contact with inactive property_id raises ValueError."""
        from app.services.contact_service import contact_service

        phone = "+595981700202"
        contact = await self._create_base_contact(db, phone)
        await db.commit()

        with pytest.raises(ValueError, match="not found or inactive"):
            await contact_service.update_contact(
                db,
                contact_id=contact.id,
                name=None,
                phone=None,
                email=None,
                operacion=None,
                zona=None,
                presupuesto_raw="",
                dormitorios_raw="",
    
                user_id=1,
                user_email="test@onnixtest.com",
                property_id=_INACTIVE_PROPERTY_ID,
            )

    async def test_update_contact_property_id_none_is_noop(self, db):
        """update_contact with property_id=None does not modify existing property_id."""
        from app.services.contact_service import contact_service
        from app.repositories.contact_repo import contact_repo
        from app.models.contact import Contact

        phone = "+595981700203"
        # Create contact that already has a property_id set
        c = Contact(
            name="Already Has PropId",
            phone=phone,
            phone_normalized=phone,
            source="manual",
            status="new",
            property_id=_ACTIVE_PROPERTY_ID,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()
        await db.commit()

        ok, error, _ = await contact_service.update_contact(
            db,
            contact_id=c.id,
            name="New Name",
            phone=None,
            email=None,
            operacion=None,
            zona=None,
            presupuesto_raw="",
            dormitorios_raw="",

            user_id=1,
            user_email="test@onnixtest.com",
            property_id=None,
        )
        assert ok is True

        updated = await contact_repo.get_by_id(db, c.id)
        # property_id should remain unchanged when None is passed
        assert updated.property_id == _ACTIVE_PROPERTY_ID

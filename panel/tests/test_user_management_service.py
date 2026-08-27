"""
Tests for app/services/user_management_service.py

Covers: create_user (bcrypt hash), update_user, change_password, toggle_active.
"""
import bcrypt
import pytest
from app.services.user_management_service import user_management_service
from app.repositories.user_repo import user_repo


class TestCreateUser:
    async def test_creates_user_in_db(self, db):
        user = await user_management_service.create_user(
            db, email="pytest_svc_create@onnixtest.com",
            password="TestPass123!", name="Pytest Svc", role="user",
        )
        assert user.id is not None
        assert user.email == "pytest_svc_create@onnixtest.com"

    async def test_password_is_bcrypt_hashed(self, db):
        user = await user_management_service.create_user(
            db, email="pytest_svc_hash@onnixtest.com",
            password="MySecret123!", name="Hash Test", role="user",
        )
        assert user.password_hash != "MySecret123!"
        assert bcrypt.checkpw(b"MySecret123!", user.password_hash.encode("utf-8"))

    async def test_username_derived_from_email(self, db):
        user = await user_management_service.create_user(
            db, email="pytest_svc_uname@onnixtest.com",
            password="TestPass123!", name="Uname Test", role="user",
        )
        assert user.username == "pytest_svc_uname"

    async def test_role_assigned_correctly(self, db):
        user = await user_management_service.create_user(
            db, email="pytest_svc_role@onnixtest.com",
            password="TestPass123!", name="Role Test", role="admin",
        )
        assert user.role == "admin"

    async def test_new_user_is_active(self, db):
        user = await user_management_service.create_user(
            db, email="pytest_svc_active@onnixtest.com",
            password="TestPass123!", name="Active Test", role="user",
        )
        assert user.is_active is True


class TestUpdateUser:
    async def test_updates_name_and_email(self, db):
        user = await user_management_service.create_user(
            db, email="pytest_svc_upd@onnixtest.com",
            password="TestPass123!", name="Before Update", role="user",
        )
        updated = await user_management_service.update_user(
            db, user.id,
            name="After Update",
            email="pytest_svc_upd@onnixtest.com",
            role="user",
        )
        assert updated.name == "After Update"
        assert updated.display_name == "After Update"

    async def test_updates_role(self, db):
        user = await user_management_service.create_user(
            db, email="pytest_svc_updrole@onnixtest.com",
            password="TestPass123!", name="Role Before", role="user",
        )
        updated = await user_management_service.update_user(
            db, user.id, name="Role Before",
            email="pytest_svc_updrole@onnixtest.com", role="admin",
        )
        assert updated.role == "admin"

    async def test_nonexistent_user_returns_none(self, db):
        result = await user_management_service.update_user(
            db, 999999, name="X", email="x@x.com", role="user",
        )
        assert result is None


class TestChangePassword:
    async def test_password_hash_changes(self, db):
        user = await user_management_service.create_user(
            db, email="pytest_svc_pwd@onnixtest.com",
            password="OldPass123!", name="Pwd Test", role="user",
        )
        old_hash = user.password_hash
        updated = await user_management_service.change_password(
            db, user.id, "NewPass456!",
        )
        assert updated.password_hash != old_hash

    async def test_new_password_verifies(self, db):
        user = await user_management_service.create_user(
            db, email="pytest_svc_verify@onnixtest.com",
            password="OldPass123!", name="Verify Test", role="user",
        )
        await user_management_service.change_password(db, user.id, "NewPass456!")
        refreshed = await user_repo.get_by_email(db, "pytest_svc_verify@onnixtest.com")
        assert bcrypt.checkpw(b"NewPass456!", refreshed.password_hash.encode("utf-8"))

    async def test_nonexistent_user_returns_none(self, db):
        result = await user_management_service.change_password(db, 999999, "NewPass!")
        assert result is None


class TestToggleActive:
    async def test_deactivates_active_user(self, db):
        user = await user_management_service.create_user(
            db, email="pytest_svc_tog@onnixtest.com",
            password="TestPass123!", name="Toggle Test", role="user",
        )
        assert user.is_active is True
        updated = await user_management_service.toggle_active(db, user.id)
        assert updated.is_active is False

    async def test_activates_inactive_user(self, db):
        user = await user_management_service.create_user(
            db, email="pytest_svc_tog2@onnixtest.com",
            password="TestPass123!", name="Toggle Test 2", role="user",
        )
        await user_management_service.toggle_active(db, user.id)  # deactivate
        reactivated = await user_management_service.toggle_active(db, user.id)
        assert reactivated.is_active is True

    async def test_nonexistent_user_returns_none(self, db):
        result = await user_management_service.toggle_active(db, 999999)
        assert result is None

"""
Tests for app/repositories/user_repo.py

Covers: get_all, get_by_id, get_by_email, create, update.
Test users use email pattern pytest_*@onnixtest.com (cleaned up by conftest).
"""
import bcrypt
import pytest
from app.repositories.user_repo import user_repo


class TestGetAll:
    async def test_returns_list(self, db):
        users = await user_repo.get_all(db)
        assert isinstance(users, list)

    async def test_includes_existing_users(self, db):
        # Post commit 21bebd3 (UX bug9): get_all defaults to active=True.
        # la administradora is inactive in fixtures — query with active=None to see all.
        users = await user_repo.get_all(db, active=None)
        emails = [u.email for u in users]
        assert "ez@onnix.com.py" in emails
        assert "operaciones@onnix.com.py" in emails

    async def test_ordered_by_created_at_desc(self, db):
        # Post UX refactor (commit 21bebd3): users ordered by created_at DESC
        # NULLS LAST, id DESC tie-breaker — most recently created shows first.
        users = await user_repo.get_all(db)
        with_dates = [u for u in users if u.created_at is not None]
        for prev, cur in zip(with_dates, with_dates[1:]):
            assert prev.created_at >= cur.created_at


class TestGetById:
    async def test_existing_user(self, db):
        users = await user_repo.get_all(db)
        ez = next(u for u in users if u.email == "ez@onnix.com.py")
        found = await user_repo.get_by_id(db, ez.id)
        assert found is not None
        assert found.email == "ez@onnix.com.py"

    async def test_nonexistent_id_returns_none(self, db):
        result = await user_repo.get_by_id(db, 999999)
        assert result is None


class TestGetByEmail:
    async def test_existing_email(self, db):
        user = await user_repo.get_by_email(db, "ez@onnix.com.py")
        assert user is not None
        assert user.role == "admin"

    async def test_nonexistent_email_returns_none(self, db):
        result = await user_repo.get_by_email(db, "noexiste@onnixtest.com")
        assert result is None

    async def test_case_sensitive(self, db):
        result = await user_repo.get_by_email(db, "EZ@ONNIXSA.COM.PY")
        assert result is None


class TestCreate:
    async def test_creates_user_with_correct_fields(self, db):
        user = await user_repo.create(
            db,
            email="pytest_create@onnixtest.com",
            password_hash=bcrypt.hashpw(b"Test1234!", bcrypt.gensalt()).decode("utf-8"),
            name="Pytest Create",
            display_name="Pytest Create",
            username="pytest_create",
            role="user",
        )
        assert user.id is not None
        assert user.email == "pytest_create@onnixtest.com"
        assert user.role == "user"
        assert user.is_active is True

    async def test_created_user_is_retrievable(self, db):
        await user_repo.create(
            db,
            email="pytest_retrieve@onnixtest.com",
            password_hash=bcrypt.hashpw(b"Test1234!", bcrypt.gensalt()).decode("utf-8"),
            name="Pytest Retrieve",
            display_name="Pytest Retrieve",
            username="pytest_retrieve",
            role="user",
        )
        found = await user_repo.get_by_email(db, "pytest_retrieve@onnixtest.com")
        assert found is not None
        assert found.name == "Pytest Retrieve"


class TestUpdate:
    async def test_updates_name(self, db):
        user = await user_repo.create(
            db,
            email="pytest_update@onnixtest.com",
            password_hash=bcrypt.hashpw(b"Test1234!", bcrypt.gensalt()).decode("utf-8"),
            name="Original Name",
            display_name="Original Name",
            username="pytest_update",
            role="user",
        )
        updated = await user_repo.update(db, user, name="Updated Name", display_name="Updated Name")
        assert updated.name == "Updated Name"

    async def test_updates_role(self, db):
        user = await user_repo.create(
            db,
            email="pytest_role@onnixtest.com",
            password_hash=bcrypt.hashpw(b"Test1234!", bcrypt.gensalt()).decode("utf-8"),
            name="Role Test",
            display_name="Role Test",
            username="pytest_role",
            role="user",
        )
        updated = await user_repo.update(db, user, role="admin")
        assert updated.role == "admin"

    async def test_toggle_is_active(self, db):
        user = await user_repo.create(
            db,
            email="pytest_active@onnixtest.com",
            password_hash=bcrypt.hashpw(b"Test1234!", bcrypt.gensalt()).decode("utf-8"),
            name="Active Test",
            display_name="Active Test",
            username="pytest_active",
            role="user",
        )
        assert user.is_active is True
        updated = await user_repo.update(db, user, is_active=False)
        assert updated.is_active is False

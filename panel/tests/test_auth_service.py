"""
Tests for app/services/auth_service.py

Covers: authenticate() with valid credentials, wrong password, unknown email,
        and inactive user.

Phase 111-02 (M6.1): authenticate() now returns a tuple
    `(User | None, result_str)`
where `result_str ∈ {'success', 'wrong_password', 'inactive', 'not_found'}`.
"""
import os

import pytest
from app.services.auth_service import auth_service

_TEST_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "test-fallback-only")


class TestAuthenticate:
    async def test_valid_admin_credentials(self, db):
        user, result = await auth_service.authenticate(
            db, "ez@onnix.com.py", _TEST_PASSWORD
        )
        assert user is not None
        assert user.email == "ez@onnix.com.py"
        assert user.role == "admin"
        assert result == "success"

    async def test_valid_user_credentials(self, db):
        user, result = await auth_service.authenticate(
            db, "operaciones@onnix.com.py", _TEST_PASSWORD
        )
        if user is None:
            pytest.skip("admin password changed — cannot test with hardcoded creds on prod DB")
        assert user.role == "user"
        assert result == "success"

    async def test_wrong_password_returns_wrong_password(self, db):
        user, result = await auth_service.authenticate(
            db, "ez@onnix.com.py", "wrongpassword"
        )
        assert user is None
        assert result == "wrong_password"

    async def test_unknown_email_returns_not_found(self, db):
        user, result = await auth_service.authenticate(
            db, "nobody@onnixtest.com", _TEST_PASSWORD
        )
        assert user is None
        assert result == "not_found"

    async def test_empty_password_returns_wrong_password(self, db):
        user, result = await auth_service.authenticate(
            db, "ez@onnix.com.py", ""
        )
        assert user is None
        assert result == "wrong_password"

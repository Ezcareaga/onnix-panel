"""
Tests for timing-side-channel fix in app/services/auth_service.py (Fix C3).

When an email is not found, bcrypt.checkpw must still be called once with the
dummy hash so the response time is indistinguishable from a wrong-password path.
"""
import pytest
from unittest.mock import patch, call


class TestTimingEqualization:
    async def test_bcrypt_called_on_not_found_path(self, db):
        """bcrypt.checkpw must be invoked exactly once when email is unknown."""
        from app.services import auth_service as auth_mod

        with patch.object(auth_mod.bcrypt, "checkpw", wraps=auth_mod.bcrypt.checkpw) as spy:
            user, result = await auth_mod.auth_service.authenticate(
                db, "totally_unknown_pytest@onnixtest.com", "any-password"
            )

        assert result == "not_found"
        assert user is None
        # Must have been called exactly once (the dummy equalizer call)
        assert spy.call_count == 1, (
            f"Expected bcrypt.checkpw called once on not_found path, got {spy.call_count}"
        )

    async def test_not_found_still_returns_none_and_not_found(self, db):
        """The dummy bcrypt call must NOT change the return contract."""
        from app.services.auth_service import auth_service

        user, result = await auth_service.authenticate(
            db, "nobody_pytest_timing@onnixtest.com", "irrelevant"
        )
        assert user is None
        assert result == "not_found"

    async def test_wrong_password_also_calls_bcrypt(self, db):
        """Wrong-password path calls bcrypt.checkpw exactly once (real check)."""
        from app.services import auth_service as auth_mod

        with patch.object(auth_mod.bcrypt, "checkpw", wraps=auth_mod.bcrypt.checkpw) as spy:
            user, result = await auth_mod.auth_service.authenticate(
                db, "ez@onnix.com.py", "definitley_wrong_pw"
            )

        assert result == "wrong_password"
        assert user is None
        assert spy.call_count == 1

    async def test_success_path_unchanged(self, db):
        """Successful auth still works and returns (user, 'success')."""
        import os
        from app.services.auth_service import auth_service

        pw = os.environ.get("TEST_ADMIN_PASSWORD", "test-fallback-only")
        user, result = await auth_service.authenticate(db, "ez@onnix.com.py", pw)
        assert result == "success"
        assert user is not None
        assert user.email == "ez@onnix.com.py"

    async def test_bcrypt_called_on_inactive_path(self, db):
        """bcrypt.checkpw must be invoked on the inactive path (timing equalization C3).

        An inactive account response must NOT be measurably faster than a wrong-password
        response — equalize timing by calling the dummy bcrypt.checkpw before returning.
        """
        from app.services import auth_service as auth_mod
        from unittest.mock import patch
        from sqlalchemy import text

        # Create an inactive test user
        import bcrypt as _bcrypt
        ph = _bcrypt.hashpw(b"somepassword123", _bcrypt.gensalt(rounds=4)).decode()
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active) "
            "VALUES ('pytest_timing_inactive@onnixtest.com', 'Inactive Timing', 'user', :ph, false) "
            "ON CONFLICT (email) DO UPDATE SET is_active = false, password_hash = :ph"
        ), {"ph": ph})
        await db.commit()

        with patch.object(auth_mod.bcrypt, "checkpw", wraps=auth_mod.bcrypt.checkpw) as spy:
            user, result = await auth_mod.auth_service.authenticate(
                db, "pytest_timing_inactive@onnixtest.com", "any-password"
            )

        assert result == "inactive", f"Expected 'inactive', got {result!r}"
        assert user is None
        # Timing equalizer must have been called at least once on the inactive path
        assert spy.call_count >= 1, (
            f"Expected bcrypt.checkpw called at least once on inactive path, got {spy.call_count}"
        )

    async def test_inactive_path_return_contract_unchanged(self, db):
        """The dummy bcrypt call on inactive path must NOT change the return contract."""
        from app.services.auth_service import auth_service
        from sqlalchemy import text

        import bcrypt as _bcrypt
        ph = _bcrypt.hashpw(b"somepassword123", _bcrypt.gensalt(rounds=4)).decode()
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active) "
            "VALUES ('pytest_timing_inactive2@onnixtest.com', 'Inactive Timing2', 'user', :ph, false) "
            "ON CONFLICT (email) DO UPDATE SET is_active = false, password_hash = :ph"
        ), {"ph": ph})
        await db.commit()

        user, result = await auth_service.authenticate(
            db, "pytest_timing_inactive2@onnixtest.com", "any-password"
        )
        assert user is None
        assert result == "inactive"

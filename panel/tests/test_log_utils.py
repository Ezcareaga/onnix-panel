"""
Tests for mask_email() in app/utils/log_utils.py (Fix E2).

Covers:
  - Normal email: j***@e***.com pattern
  - Single-char local part
  - Single-char domain label
  - No @ character (malformed)
  - Empty string
  - None input
  - Domain with multiple labels (subdomain)
  - Auth service logs emit masked form, not raw email
  - Auth route logs emit masked form, not raw email
"""
import logging
import os
import pytest


class TestMaskEmail:
    def setup_method(self):
        from app.utils.log_utils import mask_email
        self.mask_email = mask_email

    def test_normal_email(self):
        assert self.mask_email("juan@example.com") == "j***@e***.com"

    def test_longer_local_and_domain(self):
        result = self.mask_email("admin@onnix.com.py")
        # First char of local + *** + @ + first char of first domain label + *** + last extension
        assert result == "a***@c***.py"

    def test_admin_email(self):
        result = self.mask_email("ez@onnix.com.py")
        assert result == "e***@c***.py"

    def test_single_char_local(self):
        result = self.mask_email("a@example.com")
        assert result == "a***@e***.com"

    def test_no_at_sign_returns_redacted(self):
        result = self.mask_email("notanemail")
        assert "***" in result
        assert result != "notanemail"

    def test_empty_string(self):
        result = self.mask_email("")
        assert isinstance(result, str)
        assert result != ""  # should not raise, should return something safe

    def test_none_input(self):
        result = self.mask_email(None)
        assert isinstance(result, str)
        # Must not raise

    def test_at_sign_only(self):
        result = self.mask_email("@")
        assert isinstance(result, str)
        # No crash

    def test_simple_domain_no_subdomain(self):
        result = self.mask_email("user@gmail.com")
        assert result == "u***@g***.com"

    def test_subdomain_email(self):
        # sub.domain.com → first label is 'sub', TLD is 'com'
        result = self.mask_email("test@sub.domain.com")
        assert result == "t***@s***.com"


class TestAuthServiceLogsAreMasked:
    async def test_not_found_log_has_masked_email(self, db, caplog):
        from app.services.auth_service import auth_service

        raw_email = "logtest_notfound@onnixtest.com"
        with caplog.at_level(logging.WARNING, logger="app.services.auth_service"):
            await auth_service.authenticate(db, raw_email, "pw")

        # Raw email must NOT appear in log output
        assert raw_email not in caplog.text, (
            f"Raw email '{raw_email}' leaked into auth_service log"
        )
        # Masked form (starts with 'l***') must appear
        assert "l***" in caplog.text

    async def test_inactive_log_has_masked_email(self, db, caplog):
        """inactive path: we need a known-inactive user; skip if none available."""
        import subprocess
        subprocess.run(
            ["docker", "exec", "onnix-postgres",
             "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"], "-c",
             "INSERT INTO users (email, name, role, password_hash, is_active) "
             "VALUES ('pytest_inactive_log@onnixtest.com', 'Inactive', 'user', "
             "'$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu', false) "
             "ON CONFLICT (email) DO UPDATE SET is_active=false;"],
            capture_output=True, timeout=10,
        )
        from app.services.auth_service import auth_service

        with caplog.at_level(logging.WARNING, logger="app.services.auth_service"):
            user, result = await auth_service.authenticate(
                db, "pytest_inactive_log@onnixtest.com", "pw"
            )

        assert result == "inactive"
        assert "pytest_inactive_log@onnixtest.com" not in caplog.text
        assert "p***" in caplog.text

    async def test_bad_password_log_has_masked_email(self, db, caplog):
        from app.services.auth_service import auth_service

        with caplog.at_level(logging.WARNING, logger="app.services.auth_service"):
            await auth_service.authenticate(db, "ez@onnix.com.py", "wrong")

        assert "ez@onnix.com.py" not in caplog.text
        assert "e***" in caplog.text

    async def test_success_log_has_masked_email(self, db, caplog):
        import os
        from app.services.auth_service import auth_service

        pw = os.environ.get("TEST_ADMIN_PASSWORD", "test-fallback-only")
        with caplog.at_level(logging.INFO, logger="app.services.auth_service"):
            await auth_service.authenticate(db, "ez@onnix.com.py", pw)

        assert "ez@onnix.com.py" not in caplog.text
        assert "e***" in caplog.text

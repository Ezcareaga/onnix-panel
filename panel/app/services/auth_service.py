import logging

import bcrypt
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.user_repo import user_repo
from app.models.user import User
from app.utils.log_utils import mask_email

logger = logging.getLogger(__name__)

# Timing equalizer — pre-computed dummy hash to run a bcrypt.checkpw on the
# not-found path so it takes the same time as a wrong-password check.
# Cost factor 12 matches bcrypt.gensalt() default used in user_management_service.
_DUMMY_HASH: bytes = bcrypt.hashpw(b"__onnix_dummy_timing_equalizer__", bcrypt.gensalt(rounds=12))


class AuthService:
    @staticmethod
    async def authenticate(
        db: AsyncSession, email: str, password: str
    ) -> tuple[User | None, str]:
        """Authenticate `email` + `password` against the users table.

        Returns a tuple `(user, result)` where `result` is one of:
          - 'success'        → match (user is the authenticated User)
          - 'not_found'      → no row for that email
          - 'inactive'       → row exists but is_active is False
          - 'wrong_password' → row exists, active, but password mismatch

        Refactored in Phase 111-02: the previous `User | None` return type
        could not distinguish failure causes, which is needed for auth_audit
        and lockout (D-2). Callers now have the result string available for
        `lockout_service.record_attempt`.
        """
        user = await user_repo.get_by_email(db, email)
        if not user:
            # Timing equalization: run a dummy bcrypt check so this path takes
            # the same wall-clock time as the wrong-password path, preventing
            # email enumeration via response-time side-channel.
            bcrypt.checkpw(password.encode("utf-8"), _DUMMY_HASH)
            logger.warning("Auth failed: email=%s (not found)", mask_email(email))
            return None, "not_found"
        if not user.is_active:
            # Timing equalization: run a dummy bcrypt check so this path takes
            # the same wall-clock time as the wrong-password path, preventing
            # account-existence enumeration via response-time side-channel.
            bcrypt.checkpw(password.encode("utf-8"), _DUMMY_HASH)
            logger.warning("Auth failed: email=%s (inactive)", mask_email(email))
            return None, "inactive"
        try:
            ok = bcrypt.checkpw(
                password.encode("utf-8"), user.password_hash.encode("utf-8")
            )
        except (ValueError, AttributeError):
            # malformed hash or password edge cases → treat as wrong password
            ok = False
        if not ok:
            logger.warning("Auth failed: email=%s (bad password)", mask_email(email))
            return None, "wrong_password"
        logger.info("User authenticated: email=%s", mask_email(user.email))
        return user, "success"

auth_service = AuthService()

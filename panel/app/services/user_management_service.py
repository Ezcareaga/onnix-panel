import logging
from datetime import datetime, timezone

import bcrypt
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.user_repo import user_repo
from app.models.user import User

logger = logging.getLogger(__name__)


class UserManagementService:
    @staticmethod
    async def get_all(
        db: AsyncSession,
        search: str | None = None,
        role: str | None = None,
        active: bool | None = True,
    ) -> list[User]:
        return await user_repo.get_all(db, search=search, role=role, active=active)

    @staticmethod
    async def create_user(
        db: AsyncSession,
        email: str,
        password: str,
        name: str,
        role: str,
        display_name: str | None = None,
        phone: str | None = None,
    ) -> User:
        username = email.split("@")[0]
        password_hash = bcrypt.hashpw(
            password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
        user = await user_repo.create(
            db, email=email, password_hash=password_hash,
            name=name, display_name=display_name or name,
            username=username, role=role, phone=phone,
        )
        logger.info("User created: email=%s, role=%s", email, role)
        return user

    @staticmethod
    async def update_user(db: AsyncSession, user_id: int,
                          name: str, email: str, role: str) -> User | None:
        target = await user_repo.get_by_id(db, user_id)
        if not target:
            return None
        updated = await user_repo.update(
            db, target, name=name, display_name=name, email=email, role=role,
        )
        logger.info("User updated: user_id=%s, name=%s", user_id, name)
        return updated

    @staticmethod
    async def update_own_profile(
        db: AsyncSession,
        user: User,
        phone: str | None,
        display_name: str | None,
    ) -> User:
        """Self-service profile update — sets phone and display_name on the caller's own row.

        phone=None → clears to NULL (allowed).
        display_name=None → clears to NULL (allowed).
        No other fields are touched; email and role are immutable here.
        """
        updated = await user_repo.update(
            db, user, phone=phone, display_name=display_name
        )
        logger.info(
            "Own profile updated: user_id=%s phone_set=%s display_name_set=%s",
            user.id, phone is not None, display_name is not None,
        )
        return updated

    @staticmethod
    async def change_own_password(
        db: AsyncSession,
        user: User,
        current_password: str,
        new_password: str,
    ) -> User:
        """Self-service password change — verifies current_password first.

        Raises ValueError if current_password is incorrect.
        """
        if not bcrypt.checkpw(current_password.encode("utf-8"),
                               user.password_hash.encode("utf-8")):
            raise ValueError("Contraseña actual incorrecta")
        new_hash = bcrypt.hashpw(
            new_password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
        now = datetime.now(timezone.utc)
        updated = await user_repo.update(db, user, password_hash=new_hash, pw_changed_at=now)
        logger.info("Own password changed: user_id=%s", user.id)
        return updated

    @staticmethod
    async def change_password(db: AsyncSession, user_id: int,
                              new_password: str) -> User | None:
        target = await user_repo.get_by_id(db, user_id)
        if not target:
            return None
        new_hash = bcrypt.hashpw(
            new_password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
        now = datetime.now(timezone.utc)
        updated = await user_repo.update(
            db, target, password_hash=new_hash, pw_changed_at=now,
        )
        logger.info("Password changed: user_id=%s", user_id)
        return updated

    @staticmethod
    async def toggle_active(db: AsyncSession, user_id: int) -> User | None:
        target = await user_repo.get_by_id(db, user_id)
        if not target:
            return None
        new_state = not target.is_active
        updated = await user_repo.update(db, target, is_active=new_state)
        logger.info("User toggled active: user_id=%s, is_active=%s", user_id, new_state)
        return updated

    @staticmethod
    async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
        return await user_repo.get_by_id(db, user_id)

user_management_service = UserManagementService()

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User

class UserRepository:
    @staticmethod
    async def get_by_id(db: AsyncSession, user_id: int) -> User | None:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_email(db: AsyncSession, email: str) -> User | None:
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all(
        db: AsyncSession,
        search: str | None = None,
        role: str | None = None,
        active: bool | None = True,
    ) -> list[User]:
        q = select(User)
        if search:
            pattern = f"%{search.lower()}%"
            from sqlalchemy import or_, func
            q = q.where(
                or_(
                    func.lower(User.name).like(pattern),
                    func.lower(User.email).like(pattern),
                )
            )
        if role:
            q = q.where(User.role == role)
        if active is not None:
            q = q.where(User.is_active.is_(active))
        q = q.order_by(User.created_at.desc().nulls_last(), User.id.desc())
        result = await db.execute(q)
        return list(result.scalars().all())

    @staticmethod
    async def create(
        db: AsyncSession,
        email: str,
        password_hash: str,
        name: str,
        display_name: str,
        username: str,
        role: str,
        phone: str | None = None,
    ) -> User:
        user = User(
            email=email,
            password_hash=password_hash,
            name=name,
            display_name=display_name,
            username=username,
            role=role,
            is_active=True,
            phone=phone,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def update(db: AsyncSession, user: User, **fields) -> User:
        for key, value in fields.items():
            setattr(user, key, value)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def list_active_assignable(db: AsyncSession) -> list[User]:
        """Plan 111-03 — listar users role IN ('admin','agent') AND is_active=TRUE.

        Usado por /leads para poblar el dropdown "Asignar a…" (admin) y por
        agent-assign para validar que target_user_id apunta a un user vivo.
        Ordenado por display_name (NULLs last) y name para UX estable.
        """
        result = await db.execute(
            select(User)
            .where(User.role.in_(("admin", "agent")))
            .where(User.is_active.is_(True))
            .order_by(User.display_name.asc().nulls_last(), User.name.asc())
        )
        return list(result.scalars().all())


user_repo = UserRepository()

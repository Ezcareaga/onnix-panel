from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.bot_setting import BotSetting

class BotSettingRepository:
    @staticmethod
    async def get_value(db: AsyncSession, key: str) -> str | None:
        result = await db.execute(
            select(BotSetting.value).where(BotSetting.key == key)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all(db: AsyncSession) -> list[BotSetting]:
        result = await db.execute(select(BotSetting).order_by(BotSetting.key))
        return list(result.scalars().all())

    @staticmethod
    async def update_value(db: AsyncSession, key: str, value: str, user_id: int) -> None:
        await db.execute(
            update(BotSetting)
            .where(BotSetting.key == key)
            .values(value=value, updated_by=user_id)
        )

    @staticmethod
    async def upsert(
        db: AsyncSession,
        key: str,
        value: str,
        description: str | None = None,
        user_id: int | None = None,
    ) -> None:
        """Insert or update a bot_setting row.

        Uses INSERT ON CONFLICT (key) DO UPDATE. Safe for concurrent calls.
        Raises ValueError if value is None (bot_settings.value is NOT NULL).
        """
        if value is None:
            raise ValueError(f"upsert(): value cannot be None for key={key!r}")
        await db.execute(
            text(
                "INSERT INTO bot_settings (key, value, description, updated_at, updated_by) "
                "VALUES (:key, :value, :description, NOW(), :user_id) "
                "ON CONFLICT (key) DO UPDATE SET "
                "value = EXCLUDED.value, updated_at = NOW(), "
                "description = COALESCE(EXCLUDED.description, bot_settings.description), "
                "updated_by = EXCLUDED.updated_by"
            ),
            {"key": key, "value": value, "description": description, "user_id": user_id},
        )


    @staticmethod
    async def get_bool(db: AsyncSession, key: str, default: bool = False) -> bool:
        """Read flag booleano desde bot_settings. 'true' (case-sensitive) == True.

        Devuelve ``default`` cuando la clave no existe en la tabla.
        Diseñado para ser compatible con el patrón de flags de M2/M3.
        """
        value = await BotSettingRepository.get_value(db, key)
        if value is None:
            return default
        return value == "true"


bot_setting_repo = BotSettingRepository()

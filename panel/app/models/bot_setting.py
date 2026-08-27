from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BotSetting(Base):
    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))

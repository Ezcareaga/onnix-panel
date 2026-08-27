"""BotError model -- maps the existing ``bot_errors`` table.

Records errors from bot processing (webhook failures, AI timeouts, etc.)
for monitoring, circuit-breaker logic, and admin alerting.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, Text, DateTime, text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BotError(Base):
    __tablename__ = "bot_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow: Mapped[str] = mapped_column(String(100), nullable=False)
    node: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    execution_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    chat_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        server_default=text("NOW()"),
        default=func.now(),
    )

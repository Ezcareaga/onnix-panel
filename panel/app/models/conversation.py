from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("contacts.id"))
    status: Mapped[Optional[str]] = mapped_column(String(20))
    channel: Mapped[Optional[str]] = mapped_column(String(20))
    platform: Mapped[Optional[str]] = mapped_column(String(20))
    platform_chat_id: Mapped[Optional[str]] = mapped_column(String(100))
    is_bot_active: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)
    is_open: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)
    message_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_human_reply_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    search_context: Mapped[Optional[dict]] = mapped_column(JSONB)

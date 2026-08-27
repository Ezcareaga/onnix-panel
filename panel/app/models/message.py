from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, SmallInteger, String, Text, DateTime, ForeignKey, text, ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("conversations.id"))
    contact_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("contacts.id"))
    direction: Mapped[Optional[str]] = mapped_column(String(20))
    sender_type: Mapped[Optional[str]] = mapped_column(String(20))  # CHECK: 'contact', 'bot', 'agent'
    body: Mapped[Optional[str]] = mapped_column(Text)
    content: Mapped[Optional[str]] = mapped_column(Text)
    intent: Mapped[Optional[str]] = mapped_column(String(30))
    properties_shown: Mapped[Optional[list]] = mapped_column(ARRAY(Integer))
    external_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True)
    status: Mapped[Optional[str]] = mapped_column(String(20), default="sent")
    error_code: Mapped[Optional[str]] = mapped_column(String(20))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    ai_model: Mapped[Optional[str]] = mapped_column(String(50))
    ai_tokens_in: Mapped[Optional[int]] = mapped_column(Integer)
    ai_tokens_out: Mapped[Optional[int]] = mapped_column(Integer)
    ai_latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    tool_iterations: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

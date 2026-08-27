"""ORM model for contact_reminders table (C2.2)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ContactReminder(Base):
    __tablename__ = "contact_reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False)
    done_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

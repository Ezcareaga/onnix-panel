from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, Text, DateTime, ForeignKey, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ContactNote(Base):
    __tablename__ = "contact_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(Integer, ForeignKey("contacts.id"), nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"), default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"), default=func.now())

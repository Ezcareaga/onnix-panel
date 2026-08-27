from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, DateTime, ForeignKey, text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LeadEvent(Base):
    __tablename__ = "lead_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("contacts.id"))
    event_type: Mapped[Optional[str]] = mapped_column(String(50))
    old_status: Mapped[Optional[str]] = mapped_column(String(20))
    new_status: Mapped[Optional[str]] = mapped_column(String(20))
    triggered_by: Mapped[Optional[str]] = mapped_column(String(50), default="system")
    event_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"), default=func.now())

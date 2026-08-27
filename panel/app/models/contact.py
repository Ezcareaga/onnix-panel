from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    name: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[Optional[str]] = mapped_column(String(50))
    status: Mapped[Optional[str]] = mapped_column(String(20), default="new")
    agent_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"))  # GSD v17: panel user who last contacted this lead via WA. Mig 039: FK ON DELETE RESTRICT (D-1).
    # M6.1 (mig 039 Steps 7+8). agent_assigned_at se setea en
    # POST /leads/{id}/agent-assign (Phase 111-03). agent_seen_at se setea
    # cuando el agent abre el contact (Phase 111-07). Badge "nuevo" visible
    # mientras agent_assigned_at > agent_seen_at.
    agent_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    agent_assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    baja_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    phone_normalized: Mapped[Optional[str]] = mapped_column(String(20))
    source_id: Mapped[Optional[str]] = mapped_column(String(100))
    property_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("properties.id"))
    first_message: Mapped[Optional[str]] = mapped_column(Text)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_user_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    infocasas_ref: Mapped[Optional[str]] = mapped_column(String(20))
    consulta_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    preferences: Mapped[Optional[dict]] = mapped_column(JSONB)

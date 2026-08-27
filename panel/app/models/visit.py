"""SQLAlchemy ORM model for `visits` (M6.2 — Plan 114-01 §2.4).

Mirrors migration 040 schema exactly:
  - 10 columns (id, contact_id, property_id, agent_user_id, scheduled_at,
    status, source, notes, created_at, updated_at).
  - 2 CHECK constraints (status in 4 values, source in 3 values).
  - 3 FK relationships (contact, property, agent) with lazy="select".

Owned by VisitService (panel/app/services/visit_service.py). NO logic here —
this is a pure ORM mapping. Repos do CRUD; services do logic.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Integer, String, Text, DateTime, ForeignKey, CheckConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Visit(Base):
    __tablename__ = "visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    property_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("properties.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="scheduled",
    )
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="panel",
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled', 'done', 'cancelled', 'no_show')",
            name="visits_status_check",
        ),
        CheckConstraint(
            "source IN ('panel', 'bot', 'manual')",
            name="visits_source_check",
        ),
    )

    # Optional relationships — use sparingly to avoid N+1. Route handlers
    # generally prefer explicit joins. Mirror Property / Contact patterns.
    contact = relationship("Contact", lazy="select")
    property = relationship("Property", lazy="select")
    agent = relationship("User", lazy="select")

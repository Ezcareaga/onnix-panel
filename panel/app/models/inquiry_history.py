from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class InquiryHistory(Base):
    __tablename__ = "infocasas_inquiry_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    infocasas_ref: Mapped[str] = mapped_column(String(20), nullable=False)
    consulta_id: Mapped[Optional[str]] = mapped_column(String(100))
    consulta_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    property_title: Mapped[Optional[str]] = mapped_column(String(200))
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

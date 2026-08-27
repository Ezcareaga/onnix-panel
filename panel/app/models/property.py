from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Boolean, Text, Numeric, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(Text)
    url: Mapped[Optional[str]] = mapped_column(Text)
    price_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    price_pyg: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    price_currency: Mapped[Optional[str]] = mapped_column(String(3))
    city: Mapped[Optional[str]] = mapped_column(String(100))
    neighborhood: Mapped[Optional[str]] = mapped_column(String(100))
    operation: Mapped[Optional[str]] = mapped_column(String(20))
    property_type: Mapped[Optional[str]] = mapped_column(String(50))
    bedrooms: Mapped[Optional[int]] = mapped_column(SmallInteger)
    main_image_url: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)
    on_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    portal_listed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    portal_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    property_type_normalized: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("property_types.id"), nullable=True, index=False
    )

from decimal import Decimal
from typing import Optional

from sqlalchemy import Integer, String, Boolean, Text, Numeric, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class InfocasasProperty(Base):
    __tablename__ = "infocasas_properties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    infocasas_id: Mapped[str] = mapped_column(String(20), nullable=False)
    infocasas_ref: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(Text)
    url: Mapped[Optional[str]] = mapped_column(Text)
    city: Mapped[Optional[str]] = mapped_column(String(100))
    neighborhood: Mapped[Optional[str]] = mapped_column(String(100))
    property_type: Mapped[Optional[str]] = mapped_column(String(50))
    operation: Mapped[Optional[str]] = mapped_column(String(20))
    bedrooms: Mapped[Optional[int]] = mapped_column(SmallInteger)
    bathrooms: Mapped[Optional[int]] = mapped_column(SmallInteger)
    total_area_m2: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    built_area_m2: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    price_sale: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    currency_sale: Mapped[Optional[str]] = mapped_column(String(5))
    price_rent: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    currency_rent: Mapped[Optional[str]] = mapped_column(String(5))
    property_id: Mapped[Optional[int]] = mapped_column(Integer)
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)

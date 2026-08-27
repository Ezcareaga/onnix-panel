from sqlalchemy import Integer, String, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PropertyType(Base):
    """Catalog of normalized property types.

    Contains exactly 11 canonical codes used across all property sources
    (InfoCasas, manual entries, etc.). Populated via migration 019.
    """

    __tablename__ = "property_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)

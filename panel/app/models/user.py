from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, Boolean, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255))
    # Valid values (M6.1, mig 039 CHECK users_role_check): 'admin' | 'agent' | 'user'.
    # Operación: users NUNCA se borran físicamente — admin desactiva con
    # is_active=FALSE para preservar trazabilidad de contacts.agent_user_id
    # (FK ON DELETE RESTRICT, D-1). Antes de desactivar un agent con leads
    # asignados, reasignar los contacts.
    role: Mapped[Optional[str]] = mapped_column(String(20), default="user")
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)
    username: Mapped[Optional[str]] = mapped_column(String(50), unique=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(200))
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    pw_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

"""SQLAlchemy 2.0 ORM model for the anthropic_api_calls table.

Each row represents a single Anthropic API call with per-source attribution
so Ez can see where tokens are being spent (bot vs. scraper classifier vs.
lead profiler).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AnthropicApiCall(Base):
    """One row per Anthropic API call, written by the observability interceptor."""

    __tablename__ = "anthropic_api_calls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_creation_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0"))
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    conversation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

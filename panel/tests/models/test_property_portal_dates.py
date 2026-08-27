"""TDD — portal_listed_at and portal_expires_at columns on Property model.

Pure metadata checks: no DB connection required.
RED before implementation, GREEN after.
"""
from __future__ import annotations

import pytest
from sqlalchemy import DateTime

from app.models.property import Property


class TestPortalDatesColumns:
    def test_portal_listed_at_column_present(self) -> None:
        """Property.__table__ must include a portal_listed_at column."""
        assert "portal_listed_at" in Property.__table__.columns

    def test_portal_expires_at_column_present(self) -> None:
        """Property.__table__ must include a portal_expires_at column."""
        assert "portal_expires_at" in Property.__table__.columns

    def test_portal_listed_at_nullable(self) -> None:
        """portal_listed_at column must be nullable."""
        col = Property.__table__.columns["portal_listed_at"]
        assert col.nullable is True

    def test_portal_expires_at_nullable(self) -> None:
        """portal_expires_at column must be nullable."""
        col = Property.__table__.columns["portal_expires_at"]
        assert col.nullable is True

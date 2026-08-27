"""TDD — on_hold column on Property model.

Pure metadata + instance checks: no DB connection required.
RED before implementation, GREEN after.
"""
from __future__ import annotations

import pytest
from sqlalchemy import Boolean

from app.models.property import Property


class TestOnHoldColumn:
    def test_on_hold_column_present(self) -> None:
        """Property.__table__ must include an on_hold column."""
        assert "on_hold" in Property.__table__.columns

    def test_on_hold_is_boolean(self) -> None:
        """on_hold column type must be Boolean."""
        col = Property.__table__.columns["on_hold"]
        assert isinstance(col.type, Boolean)

    def test_on_hold_not_nullable(self) -> None:
        """on_hold column must be NOT NULL."""
        col = Property.__table__.columns["on_hold"]
        assert col.nullable is False

    def test_on_hold_column_default_arg_is_false(self) -> None:
        """on_hold column Python-side default must be False."""
        col = Property.__table__.columns["on_hold"]
        assert col.default is not None
        assert col.default.arg is False

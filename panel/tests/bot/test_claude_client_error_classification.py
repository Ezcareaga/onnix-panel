"""Tests for is_anthropic_api_error helper.

Distinguishes Anthropic SDK / HTTP errors (should trip circuit breaker) from
application errors like DB / validation / tool bugs (should NOT trip breaker).
See PLAN_M4_REFACTOR.md Task 2.1 and AUDIT §4.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from anthropic import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)
from pydantic import ValidationError, BaseModel
from sqlalchemy.exc import OperationalError, ProgrammingError, IntegrityError

from app.bot.ai.claude_client import is_anthropic_api_error


# ---------------------------------------------------------------------------
# Anthropic SDK errors → should return True (trip breaker)
# ---------------------------------------------------------------------------

def test_api_connection_error_is_anthropic():
    exc = APIConnectionError(request=MagicMock())
    assert is_anthropic_api_error(exc) is True


def test_rate_limit_error_is_anthropic():
    # RateLimitError needs specific construction — use __new__ to bypass __init__
    exc = RateLimitError.__new__(RateLimitError)
    assert is_anthropic_api_error(exc) is True


def test_api_status_error_is_anthropic():
    exc = APIStatusError.__new__(APIStatusError)
    assert is_anthropic_api_error(exc) is True


def test_authentication_error_is_anthropic():
    exc = AuthenticationError.__new__(AuthenticationError)
    assert is_anthropic_api_error(exc) is True


def test_bad_request_error_is_anthropic():
    exc = BadRequestError.__new__(BadRequestError)
    assert is_anthropic_api_error(exc) is True


# ---------------------------------------------------------------------------
# Application / infrastructure errors → should return False (do NOT trip)
# ---------------------------------------------------------------------------

def test_operational_error_is_not_anthropic():
    exc = OperationalError("SELECT 1", {}, Exception("server gone away"))
    assert is_anthropic_api_error(exc) is False


def test_programming_error_is_not_anthropic():
    exc = ProgrammingError("SELECT malformed", {}, Exception("syntax error"))
    assert is_anthropic_api_error(exc) is False


def test_integrity_error_is_not_anthropic():
    exc = IntegrityError("INSERT", {}, Exception("unique constraint"))
    assert is_anthropic_api_error(exc) is False


def test_value_error_is_not_anthropic():
    assert is_anthropic_api_error(ValueError("bad ordinal")) is False


def test_key_error_is_not_anthropic():
    assert is_anthropic_api_error(KeyError("missing")) is False


def test_type_error_is_not_anthropic():
    assert is_anthropic_api_error(TypeError("wrong type")) is False


def test_pydantic_validation_error_is_not_anthropic():
    class _Model(BaseModel):
        x: int

    try:
        _Model(x="no es int")  # type: ignore[arg-type]
    except ValidationError as exc:
        assert is_anthropic_api_error(exc) is False
    else:
        pytest.fail("ValidationError no se disparó en fixture")


def test_generic_exception_is_not_anthropic():
    assert is_anthropic_api_error(Exception("whatever")) is False


def test_runtime_error_is_not_anthropic():
    assert is_anthropic_api_error(RuntimeError("boom")) is False

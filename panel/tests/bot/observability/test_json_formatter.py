"""Tests for app.bot.observability.json_formatter.JsonFormatter."""
from __future__ import annotations

import json
import logging
import re

import pytest

from app.bot.observability.context import clear_request_context, set_request_context
from app.bot.observability.json_formatter import JsonFormatter


@pytest.fixture(autouse=True)
def _reset_context():
    clear_request_context()
    yield
    clear_request_context()


def _make_record(
    name: str = "app.bot.test",
    msg: str = "hello",
    level: int = logging.INFO,
    extra: dict | None = None,
    exc_info=None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    return record


def test_emits_valid_json_with_required_fields():
    fmt = JsonFormatter()
    record = _make_record()
    output = fmt.format(record)
    data = json.loads(output)
    assert "ts" in data
    assert "level" in data
    assert "logger" in data
    assert "msg" in data
    assert data["msg"] == "hello"
    assert data["level"] == "INFO"


def test_ts_is_iso8601_with_z_suffix():
    fmt = JsonFormatter()
    record = _make_record()
    data = json.loads(fmt.format(record))
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", data["ts"])


def test_logger_alias_mapped():
    fmt = JsonFormatter()
    record = _make_record(name="app.bot.webhooks.whatsapp")
    data = json.loads(fmt.format(record))
    assert data["logger"] == "bot.webhook"


def test_merges_context_vars_when_set():
    set_request_context(request_id="r1", conversation_id=7)
    fmt = JsonFormatter()
    record = _make_record()
    data = json.loads(fmt.format(record))
    assert data["request_id"] == "r1"
    assert data["conversation_id"] == 7
    # unset context vars must NOT appear
    assert "external_id" not in data
    assert "channel" not in data
    assert "phone_e164" not in data


def test_whitelists_extra_fields():
    fmt = JsonFormatter()
    record = _make_record(
        extra={"intent": "busqueda", "foo": "bar", "tool_iterations": 3}
    )
    data = json.loads(fmt.format(record))
    assert data["intent"] == "busqueda"
    assert data["tool_iterations"] == 3
    assert "foo" not in data


def test_exc_info_included():
    fmt = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        exc = sys.exc_info()

    record = _make_record(exc_info=exc)
    data = json.loads(fmt.format(record))
    assert "exc" in data
    assert "ValueError" in data["exc"]
    assert "boom" in data["exc"]


def test_survives_non_serializable_payload():
    """Non-serializable extra values are stringified via default=str, not raised."""
    fmt = JsonFormatter()

    class _Unserializable:
        pass

    record = _make_record(extra={"intent": _Unserializable()})
    # Must not raise
    output = fmt.format(record)
    data = json.loads(output)
    # value becomes the repr string via default=str
    assert "intent" in data
    assert isinstance(data["intent"], str)

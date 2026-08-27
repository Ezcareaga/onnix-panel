"""Tests for LOG_FORMAT env var handling in setup_bot_logging()."""
from __future__ import annotations

import json
import logging
import re

import pytest

from app.bot.logging_config import setup_bot_logging


@pytest.fixture(autouse=True)
def _clean_handlers():
    """Remove all handlers from app.bot before and after each test."""
    bot_logger = logging.getLogger("app.bot")
    bot_logger.handlers.clear()
    yield
    bot_logger.handlers.clear()


def test_default_format_is_text(monkeypatch, tmp_path):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    log_file = str(tmp_path / "bot.log")
    setup_bot_logging(log_file=log_file)
    bot_logger = logging.getLogger("app.bot.test_text")

    with open(log_file, encoding="utf-8") as fh:
        # clear any startup messages
        fh.read()
        bot_logger.info("hello text")
        fh.flush()

    with open(log_file, encoding="utf-8") as fh:
        lines = fh.readlines()

    # find the "hello text" line
    matches = [l for l in lines if "hello text" in l]
    assert matches, f"Expected 'hello text' in log output, got: {lines}"
    line = matches[0]
    assert re.search(r"\[.*\] \[INFO\] \[.*\] hello text", line), (
        f"Expected text format, got: {line!r}"
    )


def test_json_format_enabled_by_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_FORMAT", "json")
    log_file = str(tmp_path / "bot.log")
    setup_bot_logging(log_file=log_file)
    bot_logger = logging.getLogger("app.bot.test_json")
    bot_logger.info("hello json")

    with open(log_file, encoding="utf-8") as fh:
        lines = [l.strip() for l in fh.readlines() if l.strip()]

    json_lines = [l for l in lines if "hello json" in l]
    assert json_lines, f"Expected 'hello json' in output, got: {lines}"
    data = json.loads(json_lines[0])
    assert data["msg"] == "hello json"
    assert data["level"] == "INFO"
    assert "ts" in data
    assert "logger" in data


def test_json_format_case_insensitive(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_FORMAT", "JSON")
    log_file = str(tmp_path / "bot.log")
    setup_bot_logging(log_file=log_file)
    bot_logger = logging.getLogger("app.bot.test_json_upper")
    bot_logger.info("case insensitive")

    with open(log_file, encoding="utf-8") as fh:
        lines = [l.strip() for l in fh.readlines() if l.strip()]

    json_lines = [l for l in lines if "case insensitive" in l]
    assert json_lines, f"Expected 'case insensitive' line, got: {lines}"
    data = json.loads(json_lines[0])
    assert data["msg"] == "case insensitive"

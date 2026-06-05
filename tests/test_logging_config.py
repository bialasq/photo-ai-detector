"""Unit tests for logging_config (task 1.1.2)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import logging_config


@pytest.fixture(autouse=True)
def _reset_logging() -> None:
    logging_config.reset_logging_for_tests()
    yield
    logging_config.reset_logging_for_tests()


def test_get_log_dir_uses_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_dir = tmp_path / "custom-logs"
    monkeypatch.setenv(logging_config.LOG_DIR_ENV_VAR, str(log_dir))

    assert logging_config.get_log_dir() == log_dir.resolve()
    assert log_dir.is_dir()


def test_setup_logging_creates_rotating_backend_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(logging_config.LOG_DIR_ENV_VAR, str(tmp_path / "logs"))
    monkeypatch.delenv(logging_config.DEV_MODE_ENV_VAR, raising=False)

    log_file = logging_config.setup_logging(level="INFO")
    assert log_file.name == "backend.log"
    assert log_file.is_file()

    logger = logging.getLogger("test.logging")
    logger.info(
        "hello structured world",
        extra={"ctx": {"event": "test.event", "n": 1}},
    )

    for handler in logging.getLogger().handlers:
        handler.flush()

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 2

    payload = json.loads(lines[-1])
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logging"
    assert payload["msg"] == "hello structured world"
    assert payload["ctx"] == {"event": "test.event", "n": 1}
    assert "ts" in payload


def test_hash_path_for_log_is_stable_and_short() -> None:
    first = logging_config.hash_path_for_log(r"C:\Photos\secret\img.jpg")
    second = logging_config.hash_path_for_log(r"C:\Photos\secret\img.jpg")
    assert first == second
    assert len(first) == 16
    assert "Photos" not in first


def test_setup_logging_adds_stderr_handler_in_dev_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(logging_config.LOG_DIR_ENV_VAR, str(tmp_path / "logs"))
    monkeypatch.setenv(logging_config.DEV_MODE_ENV_VAR, "1")

    logging_config.setup_logging(level="INFO")

    handler_types = {type(handler).__name__ for handler in logging.getLogger().handlers}
    assert "RotatingFileHandler" in handler_types
    assert "StreamHandler" in handler_types

from collections.abc import Iterator
from unittest import mock

import pytest

from workers import logger as logger_module


@pytest.fixture
def guru() -> Iterator[mock.MagicMock]:
    """Stand-in for loguru, so building a logger does not touch the real sinks."""
    with mock.patch.object(logger_module, "guru_logger") as guru_logger:
        yield guru_logger


def test_get_logger_logs_to_stdout_outside_production(
    monkeypatch: pytest.MonkeyPatch, guru: mock.MagicMock
) -> None:
    monkeypatch.setenv("WORKER_ENV", "development")

    assert logger_module.get_logger() is guru
    guru.remove.assert_called_once_with()
    guru.add.assert_called_once()
    assert guru.add.call_args.kwargs["level"] == "DEBUG"


def test_get_logger_also_logs_to_a_file_in_production(
    monkeypatch: pytest.MonkeyPatch, guru: mock.MagicMock
) -> None:
    monkeypatch.setenv("WORKER_ENV", "production")

    assert logger_module.get_logger() is guru
    assert guru.add.call_count == 2
    assert guru.add.call_args.args[0] == "/config/logs/worker.log"
    assert guru.add.call_args.kwargs["level"] == "INFO"

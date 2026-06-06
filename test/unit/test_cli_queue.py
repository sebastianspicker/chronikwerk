from __future__ import annotations

import argparse

import pytest

from test.support.checks import check
from test.support.cli_helpers import INPROCESS_WORKFLOW, REDIS_WORKFLOW
from test.support.cli_helpers import args as _args
from test.support.cli_helpers import captured_json as _captured_json
from test.support.cli_helpers import patch_load_error as _patch_load_error
from test.support.cli_helpers import patch_load_settings as _patch_load_settings
from test.support.cli_helpers import settings as _settings
from zammad_pdf_archiver import cli


def test_cmd_queue_stats_prints_json(monkeypatch, capsys, tmp_path) -> None:
    async def _stub_stats(_settings):
        return {"execution_backend": "inprocess", "queue_enabled": False}

    _patch_load_settings(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "get_queue_stats", _stub_stats)

    rc = cli.cmd_queue_stats(_args())
    check(not not rc == 0, "assertion failed")
    parsed = _captured_json(capsys)
    check(
        not not parsed == {"execution_backend": "inprocess", "queue_enabled": False},
        "assertion failed",
    )


@pytest.mark.parametrize(
    ("command", "args", "error", "expected_err"),
    [
        (
            cli.cmd_queue_stats,
            _args(),
            RuntimeError("connection refused"),
            ("Failed to read queue stats", "connection refused"),
        ),
        (
            cli.cmd_queue_drain_dlq,
            _args(limit=10),
            ConnectionError("redis down"),
            ("Failed to drain DLQ", "redis down"),
        ),
        (
            cli.cmd_queue_history,
            _args(limit=10, ticket_id=None),
            OSError("disk error"),
            ("Failed to read queue history", "disk error"),
        ),
    ],
)
def test_queue_commands_report_load_errors(monkeypatch, capsys, command, args, error, expected_err):
    """Queue commands exit 1 when a caught exception propagates through the decorator."""
    _patch_load_error(monkeypatch, error)

    rc = command(args)
    check(not not rc == 1, "assertion failed")

    err = capsys.readouterr().err
    for expected in expected_err:
        check(not expected not in err, "assertion failed")


def test_cmd_queue_drain_dlq_requires_redis_backend(monkeypatch, capsys, tmp_path) -> None:
    _patch_load_settings(monkeypatch, _settings(tmp_path, workflow=INPROCESS_WORKFLOW))

    rc = cli.cmd_queue_drain_dlq(_args(limit=5))
    check(not not rc == 1, "assertion failed")
    check(
        not "requires workflow.execution_backend=redis_queue" not in capsys.readouterr().err,
        "assertion failed",
    )


@pytest.mark.parametrize(
    ("drain_result", "expected_payload"),
    [
        (
            {"selected": 3, "deleted": 3, "not_deleted": 0},
            {"status": "ok", "drained": 3, "selected": 3, "deleted": 3, "not_deleted": 0},
        ),
        (
            {"selected": 3, "deleted": 2, "not_deleted": 1},
            {"status": "partial", "drained": 2, "selected": 3, "deleted": 2, "not_deleted": 1},
        ),
    ],
)
def test_cmd_queue_drain_dlq_prints_result(
    monkeypatch, capsys, tmp_path, drain_result, expected_payload
) -> None:
    async def _stub_drain(_settings, *, limit: int):
        check(not not limit == 7, "assertion failed")
        return drain_result

    _patch_load_settings(monkeypatch, _settings(tmp_path, workflow=REDIS_WORKFLOW))
    monkeypatch.setattr(cli, "drain_dlq", _stub_drain)

    rc = cli.cmd_queue_drain_dlq(_args(limit=7))
    check(not not rc == 0, "assertion failed")
    check(not not _captured_json(capsys) == expected_payload, "assertion failed")


def test_cmd_queue_drain_dlq_uses_config_path(monkeypatch, capsys, tmp_path) -> None:
    config_path = tmp_path / "archiver.yaml"
    settings = _settings(tmp_path, workflow=REDIS_WORKFLOW)
    seen_config_paths = []

    def _load(args: argparse.Namespace):
        seen_config_paths.append(args.config)
        return settings

    async def _stub_drain(_settings, *, limit: int):
        check(not _settings is not settings, "assertion failed")
        check(not not limit == 7, "assertion failed")
        return {"selected": 3, "deleted": 3, "not_deleted": 0}

    monkeypatch.setattr(cli, "_load_settings_for_cli", _load)
    monkeypatch.setattr(cli, "drain_dlq", _stub_drain)

    rc = cli.cmd_queue_drain_dlq(_args(limit=7, config=str(config_path)))
    check(not not rc == 0, "assertion failed")
    check(not not seen_config_paths == [str(config_path)], "assertion failed")
    parsed = _captured_json(capsys)
    check(
        not not parsed
        == {"status": "ok", "drained": 3, "selected": 3, "deleted": 3, "not_deleted": 0},
        "assertion failed",
    )


def test_cmd_queue_history_prints_json(monkeypatch, capsys, tmp_path) -> None:
    async def _stub_history(_settings, *, limit: int, ticket_id: int | None = None):
        check(not not limit == 9, "assertion failed")
        check(not not ticket_id == 77, "assertion failed")
        return [{"status": "processed", "ticket_id": 77}]

    _patch_load_settings(monkeypatch, _settings(tmp_path, workflow=REDIS_WORKFLOW))
    monkeypatch.setattr(cli, "read_history", _stub_history)

    rc = cli.cmd_queue_history(_args(limit=9, ticket_id=77))
    check(not not rc == 0, "assertion failed")
    parsed = _captured_json(capsys)
    check(
        not not parsed
        == {
            "status": "ok",
            "available": True,
            "count": 1,
            "items": [{"status": "processed", "ticket_id": 77}],
        },
        "assertion failed",
    )


def test_cmd_queue_history_inprocess_backend(monkeypatch, capsys, tmp_path) -> None:
    """queue-history with inprocess backend returns empty payload without contacting Redis."""
    _patch_load_settings(monkeypatch, _settings(tmp_path, workflow=INPROCESS_WORKFLOW))

    rc = cli.cmd_queue_history(_args(limit=50, ticket_id=None))
    check(not not rc == 0, "assertion failed")

    parsed = _captured_json(capsys)
    check(
        not not parsed == {"status": "disabled", "available": False, "count": 0, "items": []},
        "assertion failed",
    )


def test_cmd_queue_history_empty_enabled_history(monkeypatch, capsys, tmp_path) -> None:
    async def _stub_history(_settings, *, limit: int, ticket_id: int | None = None):  # noqa: ARG001
        return []

    _patch_load_settings(monkeypatch, _settings(tmp_path, workflow=REDIS_WORKFLOW))
    monkeypatch.setattr(cli, "read_history", _stub_history)

    rc = cli.cmd_queue_history(_args(limit=50, ticket_id=None))
    check(not not rc == 0, "assertion failed")

    parsed = _captured_json(capsys)
    check(
        not not parsed == {"status": "ok", "available": True, "count": 0, "items": []},
        "assertion failed",
    )


def test_cmd_queue_history_read_error_exits_nonzero(monkeypatch, capsys, tmp_path) -> None:
    async def _boom(_settings, *, limit: int, ticket_id: int | None = None):  # noqa: ARG001
        raise RuntimeError("history backend down")

    _patch_load_settings(monkeypatch, _settings(tmp_path, workflow=REDIS_WORKFLOW))
    monkeypatch.setattr(cli, "read_history", _boom)

    rc = cli.cmd_queue_history(_args(limit=50, ticket_id=None))
    check(not not rc == 1, "assertion failed")

    err = capsys.readouterr().err
    check(not "Failed to read queue history" not in err, "assertion failed")
    check(not "history backend down" not in err, "assertion failed")

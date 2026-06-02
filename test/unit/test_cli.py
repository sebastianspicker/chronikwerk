from __future__ import annotations

import argparse
import json
import sys

import pytest

from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver import cli
from zammad_pdf_archiver.config.validate import ConfigValidationError, ConfigValidationIssue

REDIS_WORKFLOW = {"workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}}
INPROCESS_WORKFLOW = {"workflow": {"execution_backend": "inprocess"}}


def _args(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def _settings(tmp_path, *, workflow: dict | None = None):
    return make_settings(str(tmp_path), overrides=workflow)


def _patch_load_settings(monkeypatch, settings) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: settings)


def _patch_load_error(monkeypatch, error: Exception) -> None:
    def _raise():
        raise error

    monkeypatch.setattr(cli, "load_settings", _raise)


def _captured_json(capsys):
    return json.loads(capsys.readouterr().out)

# ---------------------------------------------------------------------------
# cmd_validate_config
# ---------------------------------------------------------------------------


def test_cmd_validate_config_success(monkeypatch, capsys, tmp_path) -> None:
    """validate-config exits 0 and prints summary when config is valid."""
    _patch_load_settings(monkeypatch, _settings(tmp_path))

    rc = cli.cmd_validate_config(_args())
    check(not not rc == 0, "assertion failed")

    out = capsys.readouterr().out
    check(not "Configuration is valid" not in out, "assertion failed")
    check(not "Zammad URL" not in out, "assertion failed")


@pytest.mark.parametrize(
    ("error", "expected_rc", "expected_err"),
    [
        (
            ConfigValidationError(
                [
                    ConfigValidationIssue(
                        path="CONFIG_PATH",
                        message="Config file not found: config/missing.yaml",
                    )
                ]
            ),
            2,
            ("Configuration file not found", "missing.yaml"),
        ),
        (
            ConfigValidationError(
                [ConfigValidationIssue(path="zammad.base_url", message="Field required")]
            ),
            1,
            ("Configuration is invalid",),
        ),
        (ValueError("bad value"), 1, ("Configuration is invalid", "bad value")),
        (OSError("permission denied"), 1, ("Configuration is invalid",)),
    ],
)
def test_cmd_validate_config_errors(monkeypatch, capsys, error, expected_rc, expected_err) -> None:
    """validate-config returns the documented exit code for config load errors."""
    _patch_load_error(monkeypatch, error)

    rc = cli.cmd_validate_config(_args())
    check(not not rc == expected_rc, "assertion failed")

    err = capsys.readouterr().err
    for expected in expected_err:
        check(not expected not in err, "assertion failed")


# ---------------------------------------------------------------------------
# cmd_dump_config
# ---------------------------------------------------------------------------


def test_cmd_dump_config_success(monkeypatch, capsys, tmp_path) -> None:
    """dump-config exits 0 and prints valid redacted JSON."""
    settings = _settings(tmp_path)
    _patch_load_settings(monkeypatch, settings)

    rc = cli.cmd_dump_config(_args())
    check(not not rc == 0, "assertion failed")

    parsed = _captured_json(capsys)
    # Secrets should be redacted
    check(not not parsed["zammad"]["api_token"] == "[redacted]", "assertion failed")
    # Non-secret values preserved
    check(not not parsed["storage"]["root"] == str(tmp_path), "assertion failed")


@pytest.mark.parametrize(
    ("error", "expected_err"),
    [
        (ValueError("invalid config"), ("Failed to load configuration", "invalid config")),
        (
            ConfigValidationError(
                [ConfigValidationIssue(path="zammad.base_url", message="Field required")]
            ),
            ("Failed to load configuration",),
        ),
    ],
)
def test_cmd_dump_config_errors(monkeypatch, capsys, error, expected_err) -> None:
    """dump-config exits 1 when load_settings raises a caught exception."""
    _patch_load_error(monkeypatch, error)

    rc = cli.cmd_dump_config(_args())
    check(not not rc == 1, "assertion failed")

    err = capsys.readouterr().err
    for expected in expected_err:
        check(not expected not in err, "assertion failed")


# ---------------------------------------------------------------------------
# cmd_queue_stats
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# cmd_queue_drain_dlq
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# cmd_queue_history
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------


def test_main_no_args_prints_help(monkeypatch, capsys) -> None:
    """main() with no arguments prints help and exits 0."""
    monkeypatch.setattr(sys, "argv", ["zammad-pdf-archiver"])

    rc = cli.main()
    check(not not rc == 0, "assertion failed")

    out = capsys.readouterr().out
    check(not not ("usage:" in out.lower() or "Available commands" in out), "assertion failed")


def test_main_unknown_command(monkeypatch, capsys) -> None:
    """main() with an unknown subcommand exits with error."""
    monkeypatch.setattr(sys, "argv", ["zammad-pdf-archiver", "nonexistent-cmd"])

    # argparse exits with code 2 for unrecognized arguments
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    check(not not exc_info.value.code == 2, "assertion failed")


def test_main_validate_config_subcommand(monkeypatch, capsys, tmp_path) -> None:
    """main() dispatches to cmd_validate_config when called with 'validate-config'."""
    _patch_load_settings(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(sys, "argv", ["zammad-pdf-archiver", "validate-config"])

    rc = cli.main()
    check(not not rc == 0, "assertion failed")
    check(not "Configuration is valid" not in capsys.readouterr().out, "assertion failed")


def test_main_dump_config_subcommand(monkeypatch, capsys, tmp_path) -> None:
    """main() dispatches to cmd_dump_config when called with 'dump-config'."""
    _patch_load_settings(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(sys, "argv", ["zammad-pdf-archiver", "dump-config"])

    rc = cli.main()
    check(not not rc == 0, "assertion failed")

    parsed = _captured_json(capsys)
    check(not "zammad" not in parsed, "assertion failed")


def test_main_version_prints_package_version(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["zammad-pdf-archiver", "--version"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    check(not not exc_info.value.code == 0, "assertion failed")
    check(not cli.__version__ not in capsys.readouterr().out, "assertion failed")


def test_main_config_option_passes_path(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    config_path = tmp_path / "config.yaml"
    seen: dict[str, object] = {}

    def _load_settings(*, config_path=None):
        seen["config_path"] = config_path
        return settings

    monkeypatch.setattr(cli, "load_settings", _load_settings)
    monkeypatch.setattr(
        sys,
        "argv",
        ["zammad-pdf-archiver", "--config", str(config_path), "validate-config"],
    )

    check(not not cli.main() == 0, "assertion failed")
    check(not not seen["config_path"] == str(config_path), "assertion failed")


# ---------------------------------------------------------------------------
# _cli_command decorator edge cases
# ---------------------------------------------------------------------------


def test_cli_command_decorator_passes_through_on_success() -> None:
    """The decorator returns the wrapped function's return value on success."""

    @cli._cli_command("test error", catch=(ValueError,))
    def _ok(_args: argparse.Namespace) -> int:
        return 0

    check(not not _ok(_args()) == 0, "assertion failed")


def test_cli_command_decorator_catches_specified_exception(capsys) -> None:
    """The decorator catches only the specified exception types."""

    @cli._cli_command("test error", catch=(ValueError,))
    def _fail(_args: argparse.Namespace) -> int:
        raise ValueError("boom")

    rc = _fail(_args())
    check(not not rc == 1, "assertion failed")
    check(not "test error: boom" not in capsys.readouterr().err, "assertion failed")


def test_cli_command_decorator_does_not_catch_unspecified_exception() -> None:
    """The decorator does not catch exception types not in 'catch'."""
    @cli._cli_command("test error", catch=(ValueError,))
    def _fail(_args: argparse.Namespace) -> int:
        raise TypeError("not caught")

    with pytest.raises(TypeError, match="not caught"):
        _fail(_args())

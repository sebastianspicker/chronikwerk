from __future__ import annotations

import argparse
import json
import sys

from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver import cli
from zammad_pdf_archiver.config.validate import ConfigValidationError, ConfigValidationIssue

# ---------------------------------------------------------------------------
# cmd_validate_config
# ---------------------------------------------------------------------------


def test_cmd_validate_config_success(monkeypatch, capsys, tmp_path) -> None:
    """validate-config exits 0 and prints summary when config is valid."""
    settings = make_settings(str(tmp_path))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    rc = cli.cmd_validate_config(argparse.Namespace())
    check(not not rc == 0, "assertion failed")

    out = capsys.readouterr().out
    check(not "Configuration is valid" not in out, "assertion failed")
    check(not "Zammad URL" not in out, "assertion failed")


def test_cmd_validate_config_file_not_found(monkeypatch, capsys) -> None:
    """validate-config exits 2 when config file is missing."""

    issue = ConfigValidationIssue(
        path="CONFIG_PATH",
        message="Config file not found: config/missing.yaml",
    )

    def _raise():
        raise ConfigValidationError([issue])

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_validate_config(argparse.Namespace())
    check(not not rc == 2, "assertion failed")

    err = capsys.readouterr().err
    check(not "Configuration file not found" not in err, "assertion failed")
    check(not "missing.yaml" not in err, "assertion failed")


def test_cmd_validate_config_invalid(monkeypatch, capsys) -> None:
    """validate-config exits 1 on ConfigValidationError."""
    issue = ConfigValidationIssue(path="zammad.base_url", message="Field required")

    def _raise():
        raise ConfigValidationError([issue])

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_validate_config(argparse.Namespace())
    check(not not rc == 1, "assertion failed")

    err = capsys.readouterr().err
    check(not "Configuration is invalid" not in err, "assertion failed")


def test_cmd_validate_config_value_error(monkeypatch, capsys) -> None:
    """validate-config exits 1 on ValueError."""

    def _raise():
        raise ValueError("bad value")

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_validate_config(argparse.Namespace())
    check(not not rc == 1, "assertion failed")

    err = capsys.readouterr().err
    check(not "Configuration is invalid" not in err, "assertion failed")
    check(not "bad value" not in err, "assertion failed")


def test_cmd_validate_config_os_error(monkeypatch, capsys) -> None:
    """validate-config exits 1 on OSError."""

    def _raise():
        raise OSError("permission denied")

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_validate_config(argparse.Namespace())
    check(not not rc == 1, "assertion failed")

    err = capsys.readouterr().err
    check(not "Configuration is invalid" not in err, "assertion failed")


# ---------------------------------------------------------------------------
# cmd_dump_config
# ---------------------------------------------------------------------------


def test_cmd_dump_config_success(monkeypatch, capsys, tmp_path) -> None:
    """dump-config exits 0 and prints valid redacted JSON."""
    settings = make_settings(str(tmp_path))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    rc = cli.cmd_dump_config(argparse.Namespace())
    check(not not rc == 0, "assertion failed")

    parsed = json.loads(capsys.readouterr().out)
    # Secrets should be redacted
    check(not not parsed["zammad"]["api_token"] == "[redacted]", "assertion failed")
    # Non-secret values preserved
    check(not not parsed["storage"]["root"] == str(tmp_path), "assertion failed")


def test_cmd_dump_config_error(monkeypatch, capsys) -> None:
    """dump-config exits 1 when load_settings raises a caught exception."""

    def _raise():
        raise ValueError("invalid config")

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_dump_config(argparse.Namespace())
    check(not not rc == 1, "assertion failed")

    err = capsys.readouterr().err
    check(not "Failed to load configuration" not in err, "assertion failed")
    check(not "invalid config" not in err, "assertion failed")


def test_cmd_dump_config_config_validation_error(monkeypatch, capsys) -> None:
    """dump-config exits 1 when load_settings raises ConfigValidationError."""
    issue = ConfigValidationIssue(path="zammad.base_url", message="Field required")

    def _raise():
        raise ConfigValidationError([issue])

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_dump_config(argparse.Namespace())
    check(not not rc == 1, "assertion failed")

    err = capsys.readouterr().err
    check(not "Failed to load configuration" not in err, "assertion failed")


# ---------------------------------------------------------------------------
# cmd_queue_stats
# ---------------------------------------------------------------------------


def test_cmd_queue_stats_prints_json(monkeypatch, capsys, tmp_path) -> None:
    settings = make_settings(str(tmp_path))

    async def _stub_stats(_settings):
        return {"execution_backend": "inprocess", "queue_enabled": False}

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "get_queue_stats", _stub_stats)

    rc = cli.cmd_queue_stats(argparse.Namespace())
    check(not not rc == 0, "assertion failed")
    parsed = json.loads(capsys.readouterr().out)
    check(
        not not parsed == {"execution_backend": "inprocess", "queue_enabled": False},
        "assertion failed",
    )


def test_cmd_queue_stats_error(monkeypatch, capsys) -> None:
    """queue-stats exits 1 when a RuntimeError is raised."""

    def _raise():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_queue_stats(argparse.Namespace())
    check(not not rc == 1, "assertion failed")

    err = capsys.readouterr().err
    check(not "Failed to read queue stats" not in err, "assertion failed")
    check(not "connection refused" not in err, "assertion failed")


# ---------------------------------------------------------------------------
# cmd_queue_drain_dlq
# ---------------------------------------------------------------------------


def test_cmd_queue_drain_dlq_requires_redis_backend(monkeypatch, capsys, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"execution_backend": "inprocess"}},
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    rc = cli.cmd_queue_drain_dlq(argparse.Namespace(limit=5))
    check(not not rc == 1, "assertion failed")
    check(
        not "requires workflow.execution_backend=redis_queue" not in capsys.readouterr().err,
        "assertion failed",
    )


def test_cmd_queue_drain_dlq_success(monkeypatch, capsys, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}
        },
    )

    async def _stub_drain(_settings, *, limit: int):
        check(not not limit == 7, "assertion failed")
        return {"selected": 3, "deleted": 3, "not_deleted": 0}

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "drain_dlq", _stub_drain)

    rc = cli.cmd_queue_drain_dlq(argparse.Namespace(limit=7))
    check(not not rc == 0, "assertion failed")
    parsed = json.loads(capsys.readouterr().out)
    check(
        not not parsed
        == {"status": "ok", "drained": 3, "selected": 3, "deleted": 3, "not_deleted": 0},
        "assertion failed",
    )


def test_cmd_queue_drain_dlq_partial_delete(monkeypatch, capsys, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}
        },
    )

    async def _stub_drain(_settings, *, limit: int):
        check(not not limit == 7, "assertion failed")
        return {"selected": 3, "deleted": 2, "not_deleted": 1}

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "drain_dlq", _stub_drain)

    rc = cli.cmd_queue_drain_dlq(argparse.Namespace(limit=7))
    check(not not rc == 0, "assertion failed")
    parsed = json.loads(capsys.readouterr().out)
    check(
        not not parsed
        == {"status": "partial", "drained": 2, "selected": 3, "deleted": 2, "not_deleted": 1},
        "assertion failed",
    )


def test_cmd_queue_drain_dlq_uses_config_path(monkeypatch, capsys, tmp_path) -> None:
    config_path = tmp_path / "archiver.yaml"
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}
        },
    )
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

    rc = cli.cmd_queue_drain_dlq(argparse.Namespace(limit=7, config=str(config_path)))
    check(not not rc == 0, "assertion failed")
    check(not not seen_config_paths == [str(config_path)], "assertion failed")
    parsed = json.loads(capsys.readouterr().out)
    check(
        not not parsed
        == {"status": "ok", "drained": 3, "selected": 3, "deleted": 3, "not_deleted": 0},
        "assertion failed",
    )


def test_cmd_queue_drain_dlq_error(monkeypatch, capsys) -> None:
    """queue-drain-dlq exits 1 when a caught exception propagates through the decorator."""

    def _raise():
        raise ConnectionError("redis down")

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_queue_drain_dlq(argparse.Namespace(limit=10))
    check(not not rc == 1, "assertion failed")

    err = capsys.readouterr().err
    check(not "Failed to drain DLQ" not in err, "assertion failed")
    check(not "redis down" not in err, "assertion failed")


# ---------------------------------------------------------------------------
# cmd_queue_history
# ---------------------------------------------------------------------------


def test_cmd_queue_history_prints_json(monkeypatch, capsys, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}
        },
    )

    async def _stub_history(_settings, *, limit: int, ticket_id: int | None = None):
        check(not not limit == 9, "assertion failed")
        check(not not ticket_id == 77, "assertion failed")
        return [{"status": "processed", "ticket_id": 77}]

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "read_history", _stub_history)

    rc = cli.cmd_queue_history(argparse.Namespace(limit=9, ticket_id=77))
    check(not not rc == 0, "assertion failed")
    parsed = json.loads(capsys.readouterr().out)
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
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"execution_backend": "inprocess"}},
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    rc = cli.cmd_queue_history(argparse.Namespace(limit=50, ticket_id=None))
    check(not not rc == 0, "assertion failed")

    parsed = json.loads(capsys.readouterr().out)
    check(
        not not parsed == {"status": "disabled", "available": False, "count": 0, "items": []},
        "assertion failed",
    )


def test_cmd_queue_history_empty_enabled_history(monkeypatch, capsys, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}
        },
    )

    async def _stub_history(_settings, *, limit: int, ticket_id: int | None = None):  # noqa: ARG001
        return []

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "read_history", _stub_history)

    rc = cli.cmd_queue_history(argparse.Namespace(limit=50, ticket_id=None))
    check(not not rc == 0, "assertion failed")

    parsed = json.loads(capsys.readouterr().out)
    check(
        not not parsed == {"status": "ok", "available": True, "count": 0, "items": []},
        "assertion failed",
    )


def test_cmd_queue_history_read_error_exits_nonzero(monkeypatch, capsys, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}
        },
    )

    async def _boom(_settings, *, limit: int, ticket_id: int | None = None):  # noqa: ARG001
        raise RuntimeError("history backend down")

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "read_history", _boom)

    rc = cli.cmd_queue_history(argparse.Namespace(limit=50, ticket_id=None))
    check(not not rc == 1, "assertion failed")

    err = capsys.readouterr().err
    check(not "Failed to read queue history" not in err, "assertion failed")
    check(not "history backend down" not in err, "assertion failed")


def test_cmd_queue_history_error(monkeypatch, capsys) -> None:
    """queue-history exits 1 when a caught exception propagates through the decorator."""

    def _raise():
        raise OSError("disk error")

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_queue_history(argparse.Namespace(limit=10, ticket_id=None))
    check(not not rc == 1, "assertion failed")

    err = capsys.readouterr().err
    check(not "Failed to read queue history" not in err, "assertion failed")
    check(not "disk error" not in err, "assertion failed")


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
    import pytest

    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    check(not not exc_info.value.code == 2, "assertion failed")


def test_main_validate_config_subcommand(monkeypatch, capsys, tmp_path) -> None:
    """main() dispatches to cmd_validate_config when called with 'validate-config'."""
    settings = make_settings(str(tmp_path))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["zammad-pdf-archiver", "validate-config"])

    rc = cli.main()
    check(not not rc == 0, "assertion failed")
    check(not "Configuration is valid" not in capsys.readouterr().out, "assertion failed")


def test_main_dump_config_subcommand(monkeypatch, capsys, tmp_path) -> None:
    """main() dispatches to cmd_dump_config when called with 'dump-config'."""
    settings = make_settings(str(tmp_path))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["zammad-pdf-archiver", "dump-config"])

    rc = cli.main()
    check(not not rc == 0, "assertion failed")

    parsed = json.loads(capsys.readouterr().out)
    check(not "zammad" not in parsed, "assertion failed")


def test_main_version_prints_package_version(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["zammad-pdf-archiver", "--version"])

    import pytest

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    check(not not exc_info.value.code == 0, "assertion failed")
    check(not cli.__version__ not in capsys.readouterr().out, "assertion failed")


def test_main_config_option_passes_path(monkeypatch, tmp_path) -> None:
    settings = make_settings(str(tmp_path))
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

    check(not not _ok(argparse.Namespace()) == 0, "assertion failed")


def test_cli_command_decorator_catches_specified_exception(capsys) -> None:
    """The decorator catches only the specified exception types."""

    @cli._cli_command("test error", catch=(ValueError,))
    def _fail(_args: argparse.Namespace) -> int:
        raise ValueError("boom")

    rc = _fail(argparse.Namespace())
    check(not not rc == 1, "assertion failed")
    check(not "test error: boom" not in capsys.readouterr().err, "assertion failed")


def test_cli_command_decorator_does_not_catch_unspecified_exception() -> None:
    """The decorator does not catch exception types not in 'catch'."""
    import pytest

    @cli._cli_command("test error", catch=(ValueError,))
    def _fail(_args: argparse.Namespace) -> int:
        raise TypeError("not caught")

    with pytest.raises(TypeError, match="not caught"):
        _fail(argparse.Namespace())

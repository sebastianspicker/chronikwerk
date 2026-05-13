from __future__ import annotations

import argparse
import json
import sys

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
    assert rc == 0

    out = capsys.readouterr().out
    assert "Configuration is valid" in out
    assert "Zammad URL" in out


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
    assert rc == 2

    err = capsys.readouterr().err
    assert "Configuration file not found" in err
    assert "missing.yaml" in err


def test_cmd_validate_config_invalid(monkeypatch, capsys) -> None:
    """validate-config exits 1 on ConfigValidationError."""
    issue = ConfigValidationIssue(path="zammad.base_url", message="Field required")

    def _raise():
        raise ConfigValidationError([issue])

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_validate_config(argparse.Namespace())
    assert rc == 1

    err = capsys.readouterr().err
    assert "Configuration is invalid" in err


def test_cmd_validate_config_value_error(monkeypatch, capsys) -> None:
    """validate-config exits 1 on ValueError."""

    def _raise():
        raise ValueError("bad value")

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_validate_config(argparse.Namespace())
    assert rc == 1

    err = capsys.readouterr().err
    assert "Configuration is invalid" in err
    assert "bad value" in err


def test_cmd_validate_config_os_error(monkeypatch, capsys) -> None:
    """validate-config exits 1 on OSError."""

    def _raise():
        raise OSError("permission denied")

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_validate_config(argparse.Namespace())
    assert rc == 1

    err = capsys.readouterr().err
    assert "Configuration is invalid" in err


# ---------------------------------------------------------------------------
# cmd_dump_config
# ---------------------------------------------------------------------------


def test_cmd_dump_config_success(monkeypatch, capsys, tmp_path) -> None:
    """dump-config exits 0 and prints valid redacted JSON."""
    settings = make_settings(str(tmp_path))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    rc = cli.cmd_dump_config(argparse.Namespace())
    assert rc == 0

    parsed = json.loads(capsys.readouterr().out)
    # Secrets should be redacted
    assert parsed["zammad"]["api_token"] == "[redacted]"
    # Non-secret values preserved
    assert parsed["storage"]["root"] == str(tmp_path)


def test_cmd_dump_config_error(monkeypatch, capsys) -> None:
    """dump-config exits 1 when load_settings raises a caught exception."""

    def _raise():
        raise ValueError("invalid config")

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_dump_config(argparse.Namespace())
    assert rc == 1

    err = capsys.readouterr().err
    assert "Failed to load configuration" in err
    assert "invalid config" in err


def test_cmd_dump_config_config_validation_error(monkeypatch, capsys) -> None:
    """dump-config exits 1 when load_settings raises ConfigValidationError."""
    issue = ConfigValidationIssue(path="zammad.base_url", message="Field required")

    def _raise():
        raise ConfigValidationError([issue])

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_dump_config(argparse.Namespace())
    assert rc == 1

    err = capsys.readouterr().err
    assert "Failed to load configuration" in err


# ---------------------------------------------------------------------------
# cmd_show_deprecated
# ---------------------------------------------------------------------------


def test_cmd_show_deprecated_no_vars(monkeypatch, capsys) -> None:
    """show-deprecated exits 0 with a clean message when no deprecated vars are set."""
    # Remove any deprecated env vars that may be present
    for old_name in cli._DEPRECATED_ALIASES:
        monkeypatch.delenv(old_name, raising=False)

    rc = cli.cmd_show_deprecated(argparse.Namespace())
    assert rc == 0

    out = capsys.readouterr().out
    assert "No deprecated environment variables in use" in out


def test_cmd_show_deprecated_with_vars_needs_migration(monkeypatch, capsys) -> None:
    """show-deprecated lists deprecated vars that need migration."""
    # Remove all deprecated vars first
    for old_name in cli._DEPRECATED_ALIASES:
        monkeypatch.delenv(old_name, raising=False)

    # Set a deprecated var WITHOUT the canonical equivalent
    monkeypatch.setenv("ZAMMAD_URL", "https://old.example.com")
    monkeypatch.delenv("ZAMMAD_BASE_URL", raising=False)

    rc = cli.cmd_show_deprecated(argparse.Namespace())
    assert rc == 0

    out = capsys.readouterr().out
    assert "Deprecated environment variables detected" in out
    assert "ZAMMAD_URL" in out
    assert "ZAMMAD_BASE_URL" in out
    assert "NEEDS MIGRATION" in out


def test_cmd_show_deprecated_with_canonical_override(monkeypatch, capsys) -> None:
    """show-deprecated shows informational status when canonical var is also set."""
    for old_name in cli._DEPRECATED_ALIASES:
        monkeypatch.delenv(old_name, raising=False)

    # Set both deprecated and canonical
    monkeypatch.setenv("ZAMMAD_URL", "https://old.example.com")
    monkeypatch.setenv("ZAMMAD_BASE_URL", "https://new.example.com")

    rc = cli.cmd_show_deprecated(argparse.Namespace())
    assert rc == 0

    out = capsys.readouterr().out
    assert "Deprecated environment variables detected" in out
    assert "ZAMMAD_URL" in out
    assert "Has canonical override" in out
    assert "removed in a future version" in out


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
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed == {"execution_backend": "inprocess", "queue_enabled": False}


def test_cmd_queue_stats_error(monkeypatch, capsys) -> None:
    """queue-stats exits 1 when a RuntimeError is raised."""

    def _raise():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_queue_stats(argparse.Namespace())
    assert rc == 1

    err = capsys.readouterr().err
    assert "Failed to read queue stats" in err
    assert "connection refused" in err


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
    assert rc == 1
    assert "requires workflow.execution_backend=redis_queue" in capsys.readouterr().err


def test_cmd_queue_drain_dlq_success(monkeypatch, capsys, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}
        },
    )

    async def _stub_drain(_settings, *, limit: int):
        assert limit == 7
        return 3

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "drain_dlq", _stub_drain)

    rc = cli.cmd_queue_drain_dlq(argparse.Namespace(limit=7))
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed == {"status": "ok", "drained": 3}


def test_cmd_queue_drain_dlq_error(monkeypatch, capsys) -> None:
    """queue-drain-dlq exits 1 when a caught exception propagates through the decorator."""

    def _raise():
        raise ConnectionError("redis down")

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_queue_drain_dlq(argparse.Namespace(limit=10))
    assert rc == 1

    err = capsys.readouterr().err
    assert "Failed to drain DLQ" in err
    assert "redis down" in err


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
        assert limit == 9
        assert ticket_id == 77
        return [{"status": "processed", "ticket_id": 77}]

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "read_history", _stub_history)

    rc = cli.cmd_queue_history(argparse.Namespace(limit=9, ticket_id=77))
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed == {
        "status": "ok",
        "count": 1,
        "items": [{"status": "processed", "ticket_id": 77}],
    }


def test_cmd_queue_history_inprocess_backend(monkeypatch, capsys, tmp_path) -> None:
    """queue-history with inprocess backend returns empty payload without contacting Redis."""
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"execution_backend": "inprocess"}},
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    rc = cli.cmd_queue_history(argparse.Namespace(limit=50, ticket_id=None))
    assert rc == 0

    parsed = json.loads(capsys.readouterr().out)
    assert parsed == {"status": "ok", "count": 0, "items": []}


def test_cmd_queue_history_error(monkeypatch, capsys) -> None:
    """queue-history exits 1 when a caught exception propagates through the decorator."""

    def _raise():
        raise OSError("disk error")

    monkeypatch.setattr(cli, "load_settings", _raise)

    rc = cli.cmd_queue_history(argparse.Namespace(limit=10, ticket_id=None))
    assert rc == 1

    err = capsys.readouterr().err
    assert "Failed to read queue history" in err
    assert "disk error" in err


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------


def test_main_no_args_prints_help(monkeypatch, capsys) -> None:
    """main() with no arguments prints help and exits 0."""
    monkeypatch.setattr(sys, "argv", ["zammad-pdf-archiver"])

    rc = cli.main()
    assert rc == 0

    out = capsys.readouterr().out
    assert "usage:" in out.lower() or "Available commands" in out


def test_main_unknown_command(monkeypatch, capsys) -> None:
    """main() with an unknown subcommand exits with error."""
    monkeypatch.setattr(sys, "argv", ["zammad-pdf-archiver", "nonexistent-cmd"])

    # argparse exits with code 2 for unrecognized arguments
    import pytest

    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 2


def test_main_validate_config_subcommand(monkeypatch, capsys, tmp_path) -> None:
    """main() dispatches to cmd_validate_config when called with 'validate-config'."""
    settings = make_settings(str(tmp_path))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["zammad-pdf-archiver", "validate-config"])

    rc = cli.main()
    assert rc == 0
    assert "Configuration is valid" in capsys.readouterr().out


def test_main_dump_config_subcommand(monkeypatch, capsys, tmp_path) -> None:
    """main() dispatches to cmd_dump_config when called with 'dump-config'."""
    settings = make_settings(str(tmp_path))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["zammad-pdf-archiver", "dump-config"])

    rc = cli.main()
    assert rc == 0

    parsed = json.loads(capsys.readouterr().out)
    assert "zammad" in parsed


def test_main_show_deprecated_subcommand(monkeypatch, capsys) -> None:
    """main() dispatches to cmd_show_deprecated when called with 'show-deprecated'."""
    for old_name in cli._DEPRECATED_ALIASES:
        monkeypatch.delenv(old_name, raising=False)
    monkeypatch.setattr(sys, "argv", ["zammad-pdf-archiver", "show-deprecated"])

    rc = cli.main()
    assert rc == 0
    assert "No deprecated environment variables" in capsys.readouterr().out


def test_main_version_prints_package_version(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["zammad-pdf-archiver", "--version"])

    import pytest

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 0
    assert cli.__version__ in capsys.readouterr().out


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

    assert cli.main() == 0
    assert seen["config_path"] == str(config_path)


# ---------------------------------------------------------------------------
# _cli_command decorator edge cases
# ---------------------------------------------------------------------------


def test_cli_command_decorator_passes_through_on_success() -> None:
    """The decorator returns the wrapped function's return value on success."""

    @cli._cli_command("test error", catch=(ValueError,))
    def _ok(_args: argparse.Namespace) -> int:
        return 0

    assert _ok(argparse.Namespace()) == 0


def test_cli_command_decorator_catches_specified_exception(capsys) -> None:
    """The decorator catches only the specified exception types."""

    @cli._cli_command("test error", catch=(ValueError,))
    def _fail(_args: argparse.Namespace) -> int:
        raise ValueError("boom")

    rc = _fail(argparse.Namespace())
    assert rc == 1
    assert "test error: boom" in capsys.readouterr().err


def test_cli_command_decorator_does_not_catch_unspecified_exception() -> None:
    """The decorator does not catch exception types not in 'catch'."""
    import pytest

    @cli._cli_command("test error", catch=(ValueError,))
    def _fail(_args: argparse.Namespace) -> int:
        raise TypeError("not caught")

    with pytest.raises(TypeError, match="not caught"):
        _fail(argparse.Namespace())

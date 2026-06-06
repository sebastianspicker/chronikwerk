from __future__ import annotations

import argparse
import sys

import pytest

from test.support.checks import check
from test.support.cli_helpers import args as _args
from test.support.cli_helpers import captured_json as _captured_json
from test.support.cli_helpers import patch_load_settings as _patch_load_settings
from test.support.cli_helpers import settings as _settings
from zammad_pdf_archiver import cli


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

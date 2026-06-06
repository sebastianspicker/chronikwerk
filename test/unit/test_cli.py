from __future__ import annotations

import pytest

from test.support.checks import check
from test.support.cli_helpers import args as _args
from test.support.cli_helpers import captured_json as _captured_json
from test.support.cli_helpers import patch_load_error as _patch_load_error
from test.support.cli_helpers import patch_load_settings as _patch_load_settings
from test.support.cli_helpers import settings as _settings
from zammad_pdf_archiver import cli
from zammad_pdf_archiver.config.validate import ConfigValidationError, ConfigValidationIssue

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

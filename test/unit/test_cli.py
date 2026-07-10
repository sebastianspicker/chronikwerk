from __future__ import annotations

import json

from zammad_pdf_archiver import cli


def test_main_without_command_prints_help(capsys, monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["zammad-pdf-archiver"])
    assert cli.main() == 0
    assert "validate-config" in capsys.readouterr().out


def test_validate_config_missing_file_returns_1(capsys, monkeypatch, tmp_path) -> None:
    missing = tmp_path / "missing.yaml"
    monkeypatch.setattr(
        "sys.argv",
        ["zammad-pdf-archiver", "validate-config", "--config", str(missing)],
    )
    assert cli.main() == 1
    assert "Config not found" in capsys.readouterr().err


def test_dump_config_redacts_secret(capsys, monkeypatch, tmp_path) -> None:
    for key in ("ZAMMAD__BASE_URL", "ZAMMAD__API_TOKEN", "STORAGE__ROOT"):
        monkeypatch.delenv(key, raising=False)
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "zammad:",
                "  base_url: https://zammad.example.local",
                "  api_token: test-token",
                "  webhook_hmac_secret: test-secret",
                "storage:",
                f"  root: {tmp_path}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config))
    monkeypatch.setattr("sys.argv", ["zammad-pdf-archiver", "dump-config"])

    assert cli.main() == 0
    data = json.loads(capsys.readouterr().out)
    assert data["zammad"]["api_token"] == "[redacted]"

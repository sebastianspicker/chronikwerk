from __future__ import annotations

from pathlib import Path

import pytest

from test.support.checks import check
from test.support.config_assertions import check_zammad_credentials
from test.support.credentials import fake_credential
from zammad_pdf_archiver.config.load import load_settings


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [
        "CONFIG_PATH",
        "ZAMMAD_BASE_URL",
        "ZAMMAD_API_TOKEN",
        "STORAGE_ROOT",
        "WEBHOOK_HMAC_SECRET",
        "HARDENING_WEBHOOK_ALLOW_UNSIGNED",
        "HARDENING_WEBHOOK_ALLOW_UNSIGNED_WHEN_NO_SECRET",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_dotenv_file_is_loaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "ZAMMAD_BASE_URL=https://zammad.example.local",
                "ZAMMAD_API_TOKEN=test-token",
                f"STORAGE_ROOT={tmp_path.as_posix()}",
                "HARDENING_WEBHOOK_ALLOW_UNSIGNED=true",
                "HARDENING_WEBHOOK_ALLOW_UNSIGNED_WHEN_NO_SECRET=true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings()
    check_zammad_credentials(
        settings,
        base_url="https://zammad.example.local",
        api_token=fake_credential("test-token"),
    )
    check(not not settings.storage.root == tmp_path, "assertion failed")

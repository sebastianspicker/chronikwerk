from __future__ import annotations

from pathlib import Path

import pytest

from zammad_pdf_archiver.config.load import load_settings


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [
        "CONFIG_PATH",
        "ZAMMAD__BASE_URL",
        "ZAMMAD__API_TOKEN",
        "STORAGE__ROOT",
        "ZAMMAD__WEBHOOK_HMAC_SECRET",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_dotenv_file_is_loaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "ZAMMAD__BASE_URL=https://zammad.example.local",
                "ZAMMAD__API_TOKEN=test-token",
                "ZAMMAD__WEBHOOK_HMAC_SECRET=test-secret",
                f"STORAGE__ROOT={tmp_path.as_posix()}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings()
    assert str(settings.zammad.base_url).rstrip("/") == "https://zammad.example.local"
    assert settings.zammad.api_token.get_secret_value() == "test-token"
    assert settings.storage.root == tmp_path
